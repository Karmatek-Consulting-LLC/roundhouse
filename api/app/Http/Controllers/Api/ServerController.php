<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\ServerOwner;
use App\Models\User;
use App\Services\Mcp\DockerException;
use App\Services\Mcp\DockerNotFoundException;
use App\Services\Mcp\EnvVar;
use App\Services\Mcp\GlobalEnvVars;
use App\Services\Mcp\ServerPermissions;
use App\Services\Mcp\ServerService;
use App\Services\Mcp\ServerSpec;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Log;
use Symfony\Component\HttpKernel\Exception\HttpException;

class ServerController extends Controller
{
    public function __construct(
        private readonly ServerService $service,
        private readonly ServerPermissions $perms,
        private readonly GlobalEnvVars $globals,
    ) {}

    // ---- Listing & reads ----

    public function index(Request $request): JsonResponse
    {
        $user = $request->user();
        $names = $this->registeredNamesForUser($user);

        $out = [];
        foreach ($names as $name) {
            $snap = $this->dockerSnapshot($name);
            $spec = $this->service->store->load($name);
            $out[] = $this->toResponse($snap, $spec);
        }
        return response()->json($out);
    }

    public function limits(): JsonResponse
    {
        return response()->json([
            'default_mcp_server_replicas' => (int) config('mcp.default_server_replicas'),
            'max_mcp_server_replicas' => (int) config('mcp.max_server_replicas'),
            'docker_swarm_mode' => $this->service->docker->swarmMode(),
        ]);
    }

    public function show(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $snap = $this->dockerSnapshot($name);
        $spec = $this->service->store->load($name);
        return response()->json($this->toResponse($snap, $spec));
    }

    public function logs(Request $request, string $name): Response
    {
        $this->assertAccess($request->user(), $name);
        $tail = (int) $request->query('tail', '200');

        if (! $this->service->docker->getServer($name)) {
            throw new HttpException(404, "No Docker service for '{$name}'; logs are unavailable until deployed.");
        }
        try {
            $text = $this->service->docker->getServerLogs($name, $tail);
        } catch (DockerNotFoundException $e) {
            throw new HttpException(404, $e->getMessage());
        } catch (DockerException $e) {
            Log::error("Failed to read logs for server '{$name}': {$e->getMessage()}");
            throw new HttpException(500, $e->getMessage());
        }
        return response($text, 200, ['Content-Type' => 'text/plain; charset=utf-8']);
    }

    // ---- Create ----

    public function store(Request $request): JsonResponse
    {
        $max = (int) config('mcp.max_server_replicas');
        $data = $request->validate([
            'name' => [
                'required', 'string',
                'regex:/^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$/',
            ],
            'description' => ['sometimes', 'string'],
            'template' => ['sometimes', 'nullable', 'string'],
            'config' => ['sometimes', 'array'],
            'replicas' => ['sometimes', 'nullable', 'integer', 'min:1', "max:{$max}"],
            'mode' => ['sometimes', 'string', 'in:structured,code'],
            'source' => ['sometimes', 'string'],
        ]);

        $mode = $data['mode'] ?? ServerSpec::MODE_STRUCTURED;
        if ($mode === ServerSpec::MODE_CODE) {
            if (empty($data['source'])) {
                throw new HttpException(422, 'source is required when mode is "code"');
            }
            if (! empty($data['template'])) {
                throw new HttpException(422, 'Cannot specify both a template and code-mode source');
            }
        }

        $user = $request->user();
        $name = $data['name'];

        if ($this->service->docker->getServer($name)) {
            throw new HttpException(409, "Server '{$name}' already exists");
        }

        $this->cleanupOrphanRegistration($name, $user);

        if (ServerOwner::query()->where('server_name', $name)->exists()) {
            throw new HttpException(409, "Server name '{$name}' is already registered");
        }

        ServerOwner::query()->create([
            'server_name' => $name,
            'owner_id' => $user->id,
        ]);

        try {
            $templateName = $data['template'] ?? null;
            $spec = new ServerSpec(
                name: $name,
                description: (string) ($data['description'] ?? ''),
                replicas: $data['replicas'] ?? null,
                mode: $mode,
                source: $mode === ServerSpec::MODE_CODE ? $data['source'] : null,
            );

            if ($templateName) {
                if (! $this->service->templates->getTemplate($templateName)) {
                    throw new HttpException(404, "Template '{$templateName}' not found");
                }
                $buildContext = $this->service->templates->render(
                    $templateName,
                    $name,
                    $data['config'] ?? [],
                );
                $this->service->store->save($spec);
                $server = $this->service->docker->buildAndStart(
                    serverName: $name,
                    buildContext: $buildContext,
                    templateName: $templateName,
                    envVars: $this->service->effectiveEnv($spec),
                    replicas: $this->service->effectiveReplicas($spec),
                    registryPrefix: $this->service->registryPrefix(),
                    registryAuth: $this->service->registryAuth(),
                );
            } else {
                $server = $this->service->buildAndDeploy($spec);
            }

            return response()->json($this->toResponse($server, $spec), 201);
        } catch (HttpException $e) {
            ServerOwner::query()->where('server_name', $name)->delete();
            throw $e;
        } catch (\Throwable $e) {
            Log::error("Failed to create server '{$name}': {$e->getMessage()}");
            ServerOwner::query()->where('server_name', $name)->delete();
            $this->service->store->delete($name);
            try {
                $this->service->docker->removeServer($name, $this->service->registryPrefix());
            } catch (\Throwable) {
                // swallow
            }
            throw new HttpException(500, $e->getMessage());
        }
    }

    // ---- Lifecycle ----

    public function start(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        if (! $this->service->docker->getServer($name)) {
            throw new HttpException(400, 'Server has no Docker service to start. Deploy configuration from the server details page first.');
        }
        $spec = $this->service->store->load($name);
        $replicas = $this->service->effectiveReplicas($spec);
        $server = $this->service->docker->startServer($name, $replicas);
        if (! $server) {
            throw new HttpException(404, "Server '{$name}' not found");
        }
        if (! $spec) {
            $spec = $this->ensureSpec($name);
        }
        return response()->json($this->toResponse($server, $spec));
    }

    public function stop(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        if (! $this->service->docker->getServer($name)) {
            throw new HttpException(400, 'Server is not deployed to Docker; nothing to stop.');
        }
        $server = $this->service->docker->stopServer($name);
        if (! $server) {
            throw new HttpException(404, "Server '{$name}' not found");
        }
        $spec = $this->service->store->load($name);
        return response()->json($this->toResponse($server, $spec));
    }

    public function redeploy(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        try {
            $server = $this->service->redeploy($spec);
            return response()->json($this->toResponse($server, $spec));
        } catch (\Throwable $e) {
            Log::error("Failed to redeploy '{$name}': {$e->getMessage()}");
            throw new HttpException(500, $e->getMessage());
        }
    }

    public function destroy(Request $request, string $name): Response
    {
        $this->assertAccess($request->user(), $name);
        $removed = false;
        try {
            $removed = $this->service->docker->removeServer($name, $this->service->registryPrefix());
        } catch (\Throwable $e) {
            Log::warning("Error removing docker server '{$name}': {$e->getMessage()}");
        }
        if (! $removed) {
            if (! ServerOwner::query()->where('server_name', $name)->exists()) {
                throw new HttpException(404, "Server '{$name}' not found");
            }
            Log::warning("Clearing orphaned registration for '{$name}' (no Docker service to remove)");
        }
        $this->service->store->delete($name);
        ServerOwner::query()->where('server_name', $name)->delete();
        return response()->noContent();
    }

    // ---- Spec mutations ----

    public function updateReplicas(Request $request, string $name): JsonResponse
    {
        $max = (int) config('mcp.max_server_replicas');
        $data = $request->validate([
            'replicas' => ['required', 'integer', 'min:1', "max:{$max}"],
        ]);

        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        $spec->replicas = (int) $data['replicas'];
        $this->service->store->save($spec);

        if ($this->service->docker->swarmMode()) {
            $running = $this->service->docker->getServer($name);
            if ($running && ($running['status'] ?? null) === 'running') {
                $this->service->docker->scaleServer($name, $spec->replicas);
            }
        }

        $server = $this->service->docker->getServer($name) ?? $this->missingSnapshot($name);
        return response()->json($this->toResponse($server, $spec));
    }

    public function updateDescription(Request $request, string $name): JsonResponse
    {
        $data = $request->validate(['description' => ['required', 'string']]);
        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        $spec->description = $data['description'];
        $this->service->store->save($spec);

        $server = $this->service->docker->getServer($name) ?? $this->missingSnapshot($name);
        return response()->json($this->toResponse($server, $spec));
    }

    public function updateSource(Request $request, string $name): JsonResponse
    {
        $data = $request->validate([
            'source' => ['required', 'string'],
        ]);
        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);

        if (! $spec->isCodeMode()) {
            throw new HttpException(409, 'Cannot set source on a structured server - use the primitive editor instead.');
        }
        $spec->source = $data['source'];
        return $this->redeployAndRespond($spec);
    }

    public function addPrimitive(Request $request, string $name): JsonResponse
    {
        $primitive = $this->validatePrimitive($request);
        $this->assertAccess($request->user(), $name);
        $this->assertScopesExist($name, $primitive['scopes'] ?? []);
        $spec = $this->ensureSpec($name);
        $this->assertStructuredMode($spec, 'add primitives');

        foreach ($spec->primitives as $p) {
            if (($p['name'] ?? null) === $primitive['name'] && ($p['kind'] ?? null) === $primitive['kind']) {
                throw new HttpException(409, "{$primitive['kind']} '{$primitive['name']}' already exists");
            }
        }
        $spec->primitives[] = $primitive;

        return $this->redeployAndRespond($spec);
    }

    public function updatePrimitive(Request $request, string $name, string $primName): JsonResponse
    {
        $primitive = $this->validatePrimitive($request);
        $this->assertAccess($request->user(), $name);
        $this->assertScopesExist($name, $primitive['scopes'] ?? []);
        $spec = $this->ensureSpec($name);
        $this->assertStructuredMode($spec, 'update primitives');

        $idx = null;
        foreach ($spec->primitives as $i => $p) {
            if (($p['name'] ?? null) === $primName) {
                $idx = $i;
                break;
            }
        }
        if ($idx === null) {
            throw new HttpException(404, "Primitive '{$primName}' not found");
        }
        $spec->primitives[$idx] = $primitive;

        return $this->redeployAndRespond($spec);
    }

    public function deletePrimitive(Request $request, string $name, string $primName): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        $this->assertStructuredMode($spec, 'delete primitives');

        $before = count($spec->primitives);
        $spec->primitives = array_values(array_filter(
            $spec->primitives,
            fn ($p) => ($p['name'] ?? null) !== $primName,
        ));
        if (count($spec->primitives) === $before) {
            throw new HttpException(404, "Primitive '{$primName}' not found");
        }

        return $this->redeployAndRespond($spec);
    }

    public function updatePackages(Request $request, string $name): JsonResponse
    {
        $data = $request->validate([
            'pip_packages' => ['required', 'array'],
            'pip_packages.*' => ['string'],
        ]);
        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        $spec->pipPackages = array_values($data['pip_packages']);
        return $this->redeployAndRespond($spec);
    }

    public function updateEnv(Request $request, string $name): JsonResponse
    {
        $data = $this->validateEnvRequest($request);
        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        $spec->envGlobalImports = ServerSpec::normalizeEnvImports($data['env_global_imports']);
        $spec->envVars = $data['env_vars'];
        return $this->redeployAndRespond($spec);
    }

    public function updateConfig(Request $request, string $name): JsonResponse
    {
        $data = $request->validate([
            'imports' => ['sometimes', 'array'],
            'imports.*' => ['string'],
            'pip_packages' => ['sometimes', 'array'],
            'pip_packages.*' => ['string'],
            'apt_packages' => ['sometimes', 'array'],
            'apt_packages.*' => ['string', 'regex:/^[a-zA-Z0-9][a-zA-Z0-9+._:=~-]*$/'],
            'env_global_imports' => ['sometimes', 'array'],
            'env_global_imports.*' => ['string'],
            'env_vars' => ['sometimes', 'array'],
            'env_vars.*.name' => ['required_with:env_vars', 'string'],
            'env_vars.*.value' => ['sometimes', 'string'],
        ]);

        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        // updateConfig rewrites imports too, which is structured-only. Packages + env are
        // available in code mode via their individual endpoints.
        $this->assertStructuredMode($spec, 'update config (imports)');
        $spec->imports = array_values($data['imports'] ?? []);
        $spec->pipPackages = array_values($data['pip_packages'] ?? []);
        $spec->aptPackages = array_values($data['apt_packages'] ?? []);
        $spec->envGlobalImports = ServerSpec::normalizeEnvImports($data['env_global_imports'] ?? []);
        $spec->envVars = $this->parseEnvVars($data['env_vars'] ?? []);
        return $this->redeployAndRespond($spec);
    }

    public function updateAptPackages(Request $request, string $name): JsonResponse
    {
        $data = $request->validate([
            'apt_packages' => ['required', 'array'],
            // Tight enough to keep this off the apt CLI's argv-injection surface
            // while still permitting versioned package names like libpq5=15.4-1.
            'apt_packages.*' => ['string', 'regex:/^[a-zA-Z0-9][a-zA-Z0-9+._:=~-]*$/'],
        ]);
        $this->assertAccess($request->user(), $name);
        $spec = $this->ensureSpec($name);
        $spec->aptPackages = array_values($data['apt_packages']);
        return $this->redeployAndRespond($spec);
    }

    // ---- Helpers ----

    private function redeployAndRespond(ServerSpec $spec): JsonResponse
    {
        try {
            $server = $this->service->redeploy($spec);
            return response()->json($this->toResponse($server, $spec));
        } catch (\Throwable $e) {
            Log::error("Redeploy failed for '{$spec->name}': {$e->getMessage()}");
            throw new HttpException(500, $e->getMessage());
        }
    }

    /** @return array<string, mixed> */
    private function validatePrimitive(Request $request): array
    {
        $data = $request->validate([
            'primitive' => ['required', 'array'],
            'primitive.kind' => ['required', 'string', 'in:tool,resource,resource_template,prompt'],
            'primitive.name' => ['required', 'string'],
            'primitive.description' => ['sometimes', 'string'],
            'primitive.code' => ['sometimes', 'string'],
            'primitive.return_type' => ['sometimes', 'string', 'in:str,dict'],
            'primitive.parameters' => ['sometimes', 'array'],
            'primitive.uri' => ['sometimes', 'string'],
            'primitive.uri_template' => ['sometimes', 'string'],
            'primitive.mime_type' => ['sometimes', 'string'],
            // Runtime scope gating - the codegen wraps the primitive in
            // @mcp.<kind>(auth=require_scopes(...)) at deploy time.
            'primitive.scopes' => ['sometimes', 'array'],
            'primitive.scopes.*' => ['string', 'max:64'],
        ]);
        return $data['primitive'];
    }

    /** @param string[] $scopes */
    private function assertScopesExist(string $server, array $scopes): void
    {
        if (! $scopes) {
            return;
        }
        $known = \App\Models\ServerScope::where('server_name', $server)
            ->whereIn('name', $scopes)
            ->pluck('name')
            ->all();
        $unknown = array_values(array_diff($scopes, $known));
        if ($unknown) {
            throw new HttpException(422, 'Unknown scopes for this server: '.implode(', ', $unknown));
        }
    }

    private function validateEnvRequest(Request $request): array
    {
        $data = $request->validate([
            'env_global_imports' => ['sometimes', 'array'],
            'env_global_imports.*' => ['string'],
            'env_vars' => ['sometimes', 'array'],
            'env_vars.*.name' => ['required_with:env_vars', 'string'],
            'env_vars.*.value' => ['sometimes', 'string'],
        ]);
        $data['env_global_imports'] = $data['env_global_imports'] ?? [];
        $data['env_vars'] = $this->parseEnvVars($data['env_vars'] ?? []);
        return $data;
    }

    /** @return EnvVar[] */
    private function parseEnvVars(array $raw): array
    {
        $out = [];
        foreach ($raw as $item) {
            if (is_array($item)) {
                $ev = EnvVar::fromArray($item);
                if ($ev) {
                    $out[] = $ev;
                }
            }
        }
        return $out;
    }

    private function assertAccess(User $user, string $serverName): void
    {
        if (! $this->perms->canAccess($user, $serverName)) {
            throw new HttpException(403, 'Access denied');
        }
    }

    private function assertStructuredMode(ServerSpec $spec, string $operation): void
    {
        if ($spec->isCodeMode()) {
            throw new HttpException(
                409,
                "Cannot {$operation} on a code-mode server - edit its server.py source instead."
            );
        }
    }

    /** @return string[] */
    private function registeredNamesForUser(User $user): array
    {
        if ($user->isSuperadmin()) {
            return ServerOwner::query()->orderBy('server_name')->pluck('server_name')->all();
        }
        $names = $this->perms->accessibleNames($user) ?? [];
        sort($names);
        return $names;
    }

    /** @return array<string, mixed> */
    private function dockerSnapshot(string $name): array
    {
        try {
            $d = $this->service->docker->getServer($name);
        } catch (\Throwable $e) {
            Log::warning("Docker get_server failed for {$name}: {$e->getMessage()}");
            return $this->unknownSnapshot($name);
        }
        return $d ?? $this->missingSnapshot($name);
    }

    /** @return array<string, mixed> */
    private function missingSnapshot(string $name): array
    {
        return [
            'name' => $name,
            'template' => 'custom',
            'status' => 'not_deployed',
            'created_at' => '',
            'replicas_running' => 0,
            'placement' => [],
        ];
    }

    /** @return array<string, mixed> */
    private function unknownSnapshot(string $name): array
    {
        return [
            'name' => $name,
            'template' => 'custom',
            'status' => 'unknown',
            'created_at' => '',
            'replicas_running' => 0,
            'placement' => [],
        ];
    }

    private function ensureSpec(string $name): ServerSpec
    {
        $spec = $this->service->store->load($name);
        if ($spec) {
            return $spec;
        }
        if ($this->service->docker->getServer($name) || ServerOwner::query()->where('server_name', $name)->exists()) {
            $spec = new ServerSpec(name: $name);
            $this->service->store->save($spec);
            return $spec;
        }
        throw new HttpException(404, "Server '{$name}' not found");
    }

    private function cleanupOrphanRegistration(string $serverName, User $user): void
    {
        if ($this->service->docker->getServer($serverName)) {
            return;
        }
        $orphan = ServerOwner::query()->where('server_name', $serverName)->first();
        if (! $orphan) {
            return;
        }
        if (! $user->isSuperadmin() && (string) $orphan->owner_id !== (string) $user->id) {
            throw new HttpException(403, "Server name '{$serverName}' is already registered to another user");
        }
        Log::warning("Removing orphaned server_owners row for '{$serverName}'");
        $orphan->delete();
        $this->service->store->delete($serverName);
    }

    /** @return array<string, mixed> */
    private function toResponse(array $server, ?ServerSpec $spec): array
    {
        $name = $server['name'] ?? '';
        $owner = ServerOwner::query()
            ->with('owner')
            ->where('server_name', $name)
            ->first();

        return [
            'name' => $name,
            'template' => $server['template'] ?? 'custom',
            'status' => $server['status'] ?? 'unknown',
            'url' => $this->service->baseUrl()."/s/{$name}/mcp",
            'description' => $spec?->description ?? '',
            'mode' => $spec?->mode ?? ServerSpec::MODE_STRUCTURED,
            'source' => $spec?->source,
            'imports' => $spec?->imports ?? [],
            'primitives' => $spec?->primitives ?? [],
            'pip_packages' => $spec?->pipPackages ?? [],
            'apt_packages' => $spec?->aptPackages ?? [],
            'env_global_imports' => $spec?->envGlobalImports ?? [],
            'env_vars' => array_map(fn (EnvVar $v) => $v->toArray(), $spec?->envVars ?? []),
            'global_env' => array_map(fn (EnvVar $v) => $v->toArray(), $this->globals->all()),
            'owner_id' => $owner ? (string) $owner->owner_id : null,
            'owner_email' => $owner?->owner?->email,
            'auth_rebuild_required_at' => $owner?->auth_rebuild_required_at?->toIso8601String(),
            'created_at' => $server['created_at'] ?? null,
            'replicas_desired' => $this->service->effectiveReplicas($spec),
            'replicas_running' => (int) ($server['replicas_running'] ?? 0),
            'docker_swarm_mode' => $this->service->docker->swarmMode(),
            'placement' => $server['placement'] ?? [],
        ];
    }
}
