<?php

namespace App\Services\Mcp;

use Illuminate\Support\Facades\Log;

/**
 * Full port of docker_manager.py - supports standalone containers and Swarm services.
 * All operations talk to the Docker daemon over /var/run/docker.sock via DockerHttp.
 */
class DockerClient
{
    public const LABEL_MANAGED = 'mcp-platform.managed';
    public const LABEL_SERVER_NAME = 'mcp-platform.server-name';
    public const LABEL_TEMPLATE = 'mcp-platform.template';
    public const CONTAINER_PREFIX = 'mcp-';

    private DockerHttp $http;
    private ?bool $swarmModeCache = null;

    public function __construct(?DockerHttp $http = null)
    {
        $this->http = $http ?? new DockerHttp((string) config('mcp.docker_socket'));
    }

    public function swarmMode(): bool
    {
        if ($this->swarmModeCache !== null) {
            return $this->swarmModeCache;
        }
        try {
            $info = $this->http->get('info');
            $state = $info['Swarm']['LocalNodeState'] ?? null;
            $this->swarmModeCache = $state === 'active';
        } catch (\Throwable) {
            $this->swarmModeCache = false;
        }
        Log::info('Docker mode: '.($this->swarmModeCache ? 'swarm' : 'standalone'));
        return $this->swarmModeCache;
    }

    // ---- Image build & push ----

    /**
     * Build an image from a build-context directory.
     * If $registryPrefix is set, also push the resulting tag.
     *
     * @param array{username:string,password:string}|null $registryAuth
     */
    public function buildImage(string $serverName, string $buildContext, ?string $registryPrefix = null, ?array $registryAuth = null): string
    {
        $tag = $this->imageTag($serverName, $registryPrefix);
        Log::info("Building image {$tag} from {$buildContext}");

        // Guzzle owns the resource once passed as body and will close it itself.
        $tar = $this->tarStream($buildContext);
        $frames = $this->http->postStream(
            'build',
            ['t' => $tag, 'rm' => '1'],
            $tar,
            ['Content-Type' => 'application/x-tar']
        );
        foreach ($frames as $frame) {
            if (isset($frame['error'])) {
                throw new DockerException("Image build failed: {$frame['error']}");
            }
        }

        if ($registryPrefix) {
            $this->pushImage($tag, $registryAuth);
        }
        return $tag;
    }

    /** @param array{username:string,password:string}|null $auth */
    public function pushImage(string $fullTag, ?array $auth = null): void
    {
        if (! str_contains($fullTag, ':')) {
            $fullTag .= ':latest';
        }
        [$repo, $tag] = $this->splitTag($fullTag);
        Log::info("Pushing image {$fullTag}");

        $headers = [];
        if ($auth) {
            $headers['X-Registry-Auth'] = $this->encodeAuth($auth);
        }

        $frames = $this->http->postStream(
            "images/{$repo}/push",
            ['tag' => $tag],
            '',
            $headers
        );
        foreach ($frames as $frame) {
            $err = $frame['error'] ?? null;
            $detail = $frame['errorDetail'] ?? null;
            if (is_array($detail)) {
                $err = $detail['message'] ?? $err;
            } elseif ($detail !== null && ! $err) {
                $err = (string) $detail;
            }
            if ($err) {
                throw new DockerException("Registry push failed: {$err}");
            }
        }
    }

    public function removeImage(string $tag): void
    {
        try {
            $this->http->delete("images/{$tag}", ['force' => '1']);
        } catch (DockerNotFoundException) {
            // already gone
        } catch (DockerException $e) {
            Log::warning("Skipping removal of image {$tag}: {$e->getMessage()}");
        }
    }

    // ---- Public unified API ----

    /**
     * @param array<string, string> $envVars
     * @param array{username:string,password:string}|null $registryAuth
     * @return array<string, mixed>
     */
    public function buildAndStart(string $serverName, string $buildContext, string $templateName, array $envVars = [], int $replicas = 1, ?string $registryPrefix = null, ?array $registryAuth = null): array
    {
        $tag = $this->buildImage($serverName, $buildContext, $registryPrefix, $registryAuth);
        if ($this->swarmMode()) {
            return $this->createService($serverName, $tag, $templateName, $envVars, $replicas);
        }
        return $this->createContainer($serverName, $tag, $templateName, $envVars);
    }

    /** @return array<int, array<string, mixed>> */
    public function listServers(): array
    {
        return $this->swarmMode() ? $this->listServices() : $this->listContainers();
    }

    /** @return array<string, mixed>|null */
    public function getServer(string $serverName): ?array
    {
        return $this->swarmMode() ? $this->getService($serverName) : $this->getContainer($serverName);
    }

    /** @return array<string, mixed>|null */
    public function startServer(string $serverName, int $replicas = 1): ?array
    {
        return $this->swarmMode() ? $this->startService($serverName, $replicas) : $this->startContainer($serverName);
    }

    /** @return array<string, mixed>|null */
    public function scaleServer(string $serverName, int $replicas): ?array
    {
        if (! $this->swarmMode()) {
            return null;
        }
        return $this->startService($serverName, $replicas);
    }

    /** @return array<string, mixed>|null */
    public function stopServer(string $serverName): ?array
    {
        return $this->swarmMode() ? $this->stopService($serverName) : $this->stopContainer($serverName);
    }

    public function removeServer(string $serverName, ?string $registryPrefix = null): bool
    {
        $result = $this->swarmMode() ? $this->removeService($serverName) : $this->removeContainer($serverName);
        $this->removeImage($this->imageTag($serverName, $registryPrefix));
        return $result;
    }

    /** @param array<string, string> $envVars @return array<string, mixed>|null */
    public function updateRuntimeEnv(string $serverName, array $envVars): ?array
    {
        $envList = [];
        foreach ($envVars as $k => $v) {
            $envList[] = "{$k}={$v}";
        }

        if ($this->swarmMode()) {
            $svc = $this->findServiceRaw($serverName);
            if (! $svc) {
                return null;
            }
            $spec = $svc['Spec'] ?? [];
            $taskTemplate = $spec['TaskTemplate'] ?? null;
            if (! is_array($taskTemplate) || ! isset($taskTemplate['ContainerSpec'])) {
                Log::warning("Swarm service {$serverName} has no ContainerSpec; cannot update env");
                return null;
            }
            $taskTemplate['ContainerSpec']['Env'] = $envList;
            $newSpec = $spec;
            $newSpec['TaskTemplate'] = $taskTemplate;
            $version = $svc['Version']['Index'] ?? 0;
            $this->http->post(
                "services/{$svc['ID']}/update",
                ['version' => $version],
                $newSpec
            );
            return $this->getService($serverName);
        }

        $name = $this->containerName($serverName);
        try {
            $container = $this->http->get("containers/{$name}/json");
        } catch (DockerNotFoundException) {
            return null;
        }
        $labels = $container['Config']['Labels'] ?? [];
        $templateName = $labels[self::LABEL_TEMPLATE] ?? 'custom';
        $image = $container['Config']['Image'] ?? $container['Image'] ?? '';

        $this->stopContainer($serverName);
        $this->removeContainer($serverName);
        return $this->createContainer($serverName, $image, $templateName, $envVars);
    }

    public function getServerLogs(string $serverName, int $tail = 200): string
    {
        $tail = max(1, min($tail, 5000));
        $query = [
            'stdout' => '1',
            'stderr' => '1',
            'tail' => (string) $tail,
            'timestamps' => '1',
        ];
        if ($this->swarmMode()) {
            $svc = $this->findServiceRaw($serverName);
            if (! $svc) {
                throw new DockerNotFoundException("Server '{$serverName}' not found");
            }
            $raw = $this->http->getRaw("services/{$svc['ID']}/logs", $query);
        } else {
            $name = $this->containerName($serverName);
            try {
                $raw = $this->http->getRaw("containers/{$name}/logs", $query);
            } catch (DockerNotFoundException) {
                throw new DockerNotFoundException("Server '{$serverName}' not found");
            }
        }
        return DockerHttp::demuxLogFrames($raw);
    }

    // ---- Container mode ----

    /** @param array<string, string> $envVars @return array<string, mixed> */
    private function createContainer(string $serverName, string $tag, string $templateName, array $envVars): array
    {
        $name = $this->containerName($serverName);
        $labels = $this->allLabels($serverName, $templateName);
        $envList = [];
        foreach ($envVars as $k => $v) {
            $envList[] = "{$k}={$v}";
        }

        Log::info("Creating container {$name}");
        $created = $this->http->post('containers/create', ['name' => $name], [
            'Image' => $tag,
            'Labels' => $labels,
            'Env' => $envList,
            'HostConfig' => [
                'NetworkMode' => config('mcp.docker_network'),
                'RestartPolicy' => ['Name' => 'unless-stopped'],
            ],
            'NetworkingConfig' => [
                'EndpointsConfig' => [
                    config('mcp.docker_network') => new \stdClass(),
                ],
            ],
        ]);
        $id = $created['Id'] ?? null;
        if (! $id) {
            throw new DockerException('Container create returned no ID');
        }
        $this->http->post("containers/{$id}/start");
        return $this->containerToDict($this->http->get("containers/{$id}/json"));
    }

    /** @return array<int, array<string, mixed>> */
    private function listContainers(): array
    {
        $filters = ['label' => [self::LABEL_MANAGED.'=true']];
        $resp = $this->http->get('containers/json', [
            'all' => '1',
            'filters' => json_encode($filters),
        ]);
        return array_map([$this, 'containerSummaryToDict'], $resp);
    }

    /** @return array<string, mixed>|null */
    private function getContainer(string $serverName): ?array
    {
        $name = $this->containerName($serverName);
        try {
            $c = $this->http->get("containers/{$name}/json");
        } catch (DockerNotFoundException) {
            return null;
        }
        $labels = $c['Config']['Labels'] ?? [];
        if (($labels[self::LABEL_MANAGED] ?? '') !== 'true') {
            return null;
        }
        return $this->containerToDict($c);
    }

    /** @return array<string, mixed>|null */
    private function startContainer(string $serverName): ?array
    {
        $name = $this->containerName($serverName);
        try {
            $this->http->post("containers/{$name}/start");
        } catch (DockerNotFoundException) {
            return null;
        } catch (DockerException $e) {
            if (str_contains($e->getMessage(), 'already started')) {
                // ignore - already running
            } else {
                throw $e;
            }
        }
        return $this->getContainer($serverName);
    }

    /** @return array<string, mixed>|null */
    private function stopContainer(string $serverName): ?array
    {
        $name = $this->containerName($serverName);
        try {
            $this->http->post("containers/{$name}/stop");
        } catch (DockerNotFoundException) {
            return null;
        } catch (DockerException) {
            // 304 = already stopped, ignore
        }
        return $this->getContainer($serverName);
    }

    private function removeContainer(string $serverName): bool
    {
        $name = $this->containerName($serverName);
        try {
            $this->http->post("containers/{$name}/stop");
        } catch (DockerException) {
            // ignore
        }
        try {
            $this->http->delete("containers/{$name}", ['force' => '1']);
            return true;
        } catch (DockerNotFoundException) {
            return false;
        }
    }

    /** @return array<string, mixed> */
    private function containerToDict(array $c): array
    {
        $labels = $c['Config']['Labels'] ?? [];
        $state = $c['State']['Status'] ?? ($c['State']['Running'] ?? false ? 'running' : 'exited');
        $running = $state === 'running';
        return [
            'name' => $labels[self::LABEL_SERVER_NAME] ?? '',
            'template' => $labels[self::LABEL_TEMPLATE] ?? '',
            'status' => $state,
            'created_at' => $c['Created'] ?? '',
            'replicas_running' => $running ? 1 : 0,
            'placement' => [],
        ];
    }

    /** Convert /containers/json summary (different shape than /containers/{id}/json). */
    private function containerSummaryToDict(array $c): array
    {
        $labels = $c['Labels'] ?? [];
        $state = $c['State'] ?? 'unknown';
        $running = $state === 'running';
        return [
            'name' => $labels[self::LABEL_SERVER_NAME] ?? '',
            'template' => $labels[self::LABEL_TEMPLATE] ?? '',
            'status' => $state,
            'created_at' => isset($c['Created']) ? gmdate('c', (int) $c['Created']) : '',
            'replicas_running' => $running ? 1 : 0,
            'placement' => [],
        ];
    }

    // ---- Swarm mode ----

    /** @param array<string, string> $envVars @return array<string, mixed> */
    private function createService(string $serverName, string $tag, string $templateName, array $envVars, int $replicas): array
    {
        $name = $this->containerName($serverName);
        $labels = $this->allLabels($serverName, $templateName);
        $envList = [];
        foreach ($envVars as $k => $v) {
            $envList[] = "{$k}={$v}";
        }

        Log::info("Creating swarm service {$name} (replicas={$replicas})");
        $spec = [
            'Name' => $name,
            'Labels' => $labels,
            'TaskTemplate' => [
                'ContainerSpec' => [
                    'Image' => $tag,
                    'Env' => $envList,
                ],
                'Networks' => [['Target' => config('mcp.docker_network')]],
            ],
            'Mode' => ['Replicated' => ['Replicas' => $replicas]],
            'EndpointSpec' => ['Mode' => 'vip'],
        ];
        $this->http->post('services/create', [], $spec);
        $got = $this->getService($serverName);
        if (! $got) {
            throw new DockerException("Swarm service {$name} missing after create");
        }
        return $got;
    }

    /** @return array<int, array<string, mixed>> */
    private function listServices(): array
    {
        $filters = ['label' => [self::LABEL_MANAGED.'=true']];
        $services = $this->http->get('services', ['filters' => json_encode($filters)]);
        return array_map(fn ($s) => $this->serviceToDict($s, includePlacement: false), $services);
    }

    /** @return array<string, mixed>|null */
    private function getService(string $serverName): ?array
    {
        $svc = $this->findServiceRaw($serverName);
        if (! $svc) {
            return null;
        }
        $labels = $svc['Spec']['Labels'] ?? [];
        if (($labels[self::LABEL_MANAGED] ?? '') !== 'true') {
            return null;
        }
        return $this->serviceToDict($svc, includePlacement: true);
    }

    /** @return array<string, mixed>|null */
    private function startService(string $serverName, int $replicas): ?array
    {
        $svc = $this->findServiceRaw($serverName);
        if (! $svc) {
            return null;
        }
        $this->scaleServiceRaw($svc, $replicas);
        return $this->getService($serverName);
    }

    /** @return array<string, mixed>|null */
    private function stopService(string $serverName): ?array
    {
        $svc = $this->findServiceRaw($serverName);
        if (! $svc) {
            return null;
        }
        $this->scaleServiceRaw($svc, 0);
        return $this->getService($serverName);
    }

    private function removeService(string $serverName): bool
    {
        $svc = $this->findServiceRaw($serverName);
        if (! $svc) {
            return false;
        }
        $this->http->delete("services/{$svc['ID']}");
        return true;
    }

    /** @return array<string, mixed>|null */
    private function findServiceRaw(string $serverName): ?array
    {
        $name = $this->containerName($serverName);
        try {
            $services = $this->http->get('services', ['filters' => json_encode(['name' => [$name]])]);
        } catch (DockerException) {
            return null;
        }
        foreach ($services as $svc) {
            if (($svc['Spec']['Name'] ?? '') === $name) {
                return $svc;
            }
        }
        return null;
    }

    private function scaleServiceRaw(array $svc, int $replicas): void
    {
        $spec = $svc['Spec'] ?? [];
        $spec['Mode'] = ['Replicated' => ['Replicas' => $replicas]];
        $version = $svc['Version']['Index'] ?? 0;
        $this->http->post(
            "services/{$svc['ID']}/update",
            ['version' => $version],
            $spec
        );
    }

    /** @return array<int, array<string, mixed>> */
    private function taskPlacementFor(array $svc): array
    {
        try {
            $tasks = $this->http->get('tasks', [
                'filters' => json_encode(['service' => [$svc['ID']]]),
            ]);
        } catch (DockerException) {
            return [];
        }

        $nodeMap = [];
        try {
            $nodes = $this->http->get('nodes');
            foreach ($nodes as $n) {
                $nodeMap[$n['ID']] = $n['Description']['Hostname'] ?? $n['ID'];
            }
        } catch (DockerException) {
            // leave map empty
        }

        $out = [];
        foreach ($tasks as $t) {
            $status = $t['Status'] ?? [];
            $err = $status['Err'] ?? null;
            if ($err === '') {
                $err = null;
            }
            $nodeId = $t['NodeID'] ?? '';
            $out[] = [
                'task_id' => $t['ID'] ?? '',
                'node_id' => $nodeId,
                'node_name' => $nodeId ? ($nodeMap[$nodeId] ?? null) : null,
                'state' => $status['State'] ?? 'unknown',
                'slot' => $t['Slot'] ?? null,
                'error' => $err,
            ];
        }
        return $out;
    }

    /** @return array<string, mixed> */
    private function serviceToDict(array $svc, bool $includePlacement): array
    {
        $spec = $svc['Spec'] ?? [];
        $labels = $spec['Labels'] ?? [];
        $replicas = $spec['Mode']['Replicated']['Replicas'] ?? 0;
        $status = $replicas > 0 ? 'running' : 'stopped';
        $placement = $includePlacement && $this->swarmMode() ? $this->taskPlacementFor($svc) : [];

        return [
            'name' => $labels[self::LABEL_SERVER_NAME] ?? '',
            'template' => $labels[self::LABEL_TEMPLATE] ?? '',
            'status' => $status,
            'created_at' => $svc['CreatedAt'] ?? '',
            'replicas_running' => (int) $replicas,
            'placement' => $placement,
        ];
    }

    // ---- Helpers ----

    public function imageTag(string $serverName, ?string $registryPrefix = null): string
    {
        $name = "mcp-server-{$serverName}";
        if ($registryPrefix) {
            $p = rtrim(trim($registryPrefix), '/');
            return "{$p}/{$name}:latest";
        }
        return "{$name}:latest";
    }

    private function containerName(string $serverName): string
    {
        return self::CONTAINER_PREFIX.$serverName;
    }

    /** @return array<string, string> */
    private function allLabels(string $serverName, string $templateName): array
    {
        return [
            self::LABEL_MANAGED => 'true',
            self::LABEL_SERVER_NAME => $serverName,
            self::LABEL_TEMPLATE => $templateName,
            ...$this->traefikLabels($serverName),
        ];
    }

    /** @return array<string, string> */
    private function traefikLabels(string $serverName): array
    {
        $router = "mcp-{$serverName}";
        return [
            'traefik.enable' => 'true',
            "traefik.http.routers.{$router}.rule" => "PathPrefix(`/s/{$serverName}`)",
            "traefik.http.middlewares.{$router}-strip.stripprefix.prefixes" => "/s/{$serverName}",
            "traefik.http.routers.{$router}.middlewares" => "{$router}-strip",
            "traefik.http.services.{$router}.loadbalancer.server.port" => '8000',
            "traefik.http.routers.{$router}.entrypoints" => (string) config('mcp.traefik_entrypoints'),
        ];
    }

    /** @return array{0:string,1:string} */
    private function splitTag(string $fullTag): array
    {
        $i = strrpos($fullTag, ':');
        if ($i === false) {
            return [$fullTag, 'latest'];
        }
        return [substr($fullTag, 0, $i), substr($fullTag, $i + 1)];
    }

    /** @param array{username:string,password:string} $auth */
    private function encodeAuth(array $auth): string
    {
        $json = json_encode([
            'username' => $auth['username'],
            'password' => $auth['password'],
        ]);
        return rtrim(strtr(base64_encode($json), '+/', '-_'), '=');
    }

    /**
     * Tar a directory into a stream for POST /build.
     * Shells out to the system `tar` binary - PharData is unusable when phar.readonly=1,
     * and the Docker build context is always the platform container where tar is guaranteed present.
     *
     * @return resource
     */
    private function tarStream(string $dir): mixed
    {
        if (! is_dir($dir)) {
            throw new DockerException("Build context not a directory: {$dir}");
        }
        $tmp = tempnam(sys_get_temp_dir(), 'mcp-build-').'.tar';
        $cmd = sprintf('tar -cf %s -C %s .', escapeshellarg($tmp), escapeshellarg($dir));
        exec($cmd, $out, $code);
        if ($code !== 0) {
            @unlink($tmp);
            throw new DockerException("tar failed (exit {$code}) for context {$dir}");
        }
        $fh = fopen($tmp, 'rb');
        if (! $fh) {
            @unlink($tmp);
            throw new DockerException("Failed to open build context tar at {$tmp}");
        }
        @unlink($tmp);
        return $fh;
    }
}
