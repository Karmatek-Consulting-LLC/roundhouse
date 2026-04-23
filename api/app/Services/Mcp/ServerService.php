<?php

namespace App\Services\Mcp;

use App\Models\PlatformSetting;
use App\Models\ServerOwner;
use Illuminate\Support\Facades\Log;

/**
 * Orchestrates spec persistence + codegen + Docker deploy for MCP servers.
 * Port of the _build_and_deploy / _redeploy / effective_env helpers in servers.py.
 */
class ServerService
{
    public function __construct(
        public readonly DockerClient $docker,
        public readonly Codegen $codegen,
        public readonly ServerStore $store,
        public readonly TemplateEngine $templates,
        public readonly GlobalEnvVars $globals,
    ) {}

    public function effectiveReplicas(?ServerSpec $spec): int
    {
        $default = (int) config('mcp.default_server_replicas', 1);
        if ($spec === null || $spec->replicas === null) {
            return $default;
        }
        return $spec->replicas;
    }

    /** @return array<string, string> */
    public function effectiveEnv(ServerSpec $spec): array
    {
        $merged = [];
        $gdict = $this->globals->asDict();
        foreach ($spec->envGlobalImports as $name) {
            if (array_key_exists($name, $gdict)) {
                $merged[$name] = $gdict[$name];
            }
        }
        foreach ($spec->envVars as $ev) {
            $merged[$ev->name] = $ev->value;
        }
        return $merged;
    }

    /** @return array<string, mixed> */
    public function buildAndDeploy(ServerSpec $spec): array
    {
        $buildContext = $this->codegen->writeBuildContext(
            $spec,
            $this->store->serverDir($spec->name),
        );
        $this->store->save($spec);

        return $this->docker->buildAndStart(
            serverName: $spec->name,
            buildContext: $buildContext,
            templateName: 'custom',
            envVars: $this->effectiveEnv($spec),
            replicas: $this->effectiveReplicas($spec),
            registryPrefix: $this->registryPrefix(),
            registryAuth: $this->registryAuth(),
        );
    }

    /** @return array<string, mixed> */
    public function redeploy(ServerSpec $spec): array
    {
        $this->docker->removeServer($spec->name, $this->registryPrefix());
        return $this->buildAndDeploy($spec);
    }

    public function registryPrefix(): ?string
    {
        $raw = trim((string) PlatformSetting::get('docker_registry', ''));
        return $raw === '' ? null : rtrim($raw, '/');
    }

    /** @return array{username:string,password:string}|null */
    public function registryAuth(): ?array
    {
        if (! $this->registryPrefix()) {
            return null;
        }
        $username = trim((string) PlatformSetting::get('docker_registry_username', ''));
        $password = trim((string) PlatformSetting::get('docker_registry_password', ''));
        if ($username === '' || $password === '') {
            return null;
        }
        return ['username' => $username, 'password' => $password];
    }

    /**
     * Push the merged env (globals + locals) into every currently-deployed MCP server
     * without rebuilding the image. Called after the platform global env changes.
     */
    public function reapplyRuntimeEnvForAllServers(): void
    {
        $names = ServerOwner::query()->orderBy('server_name')->pluck('server_name')->all();
        foreach ($names as $name) {
            $this->reapplyRuntimeEnvFor($name);
        }
    }

    public function reapplyRuntimeEnvFor(string $serverName): void
    {
        $spec = $this->store->load($serverName) ?? new ServerSpec(name: $serverName);
        if (! $this->docker->getServer($serverName)) {
            return; // not deployed - nothing to push
        }
        try {
            $this->docker->updateRuntimeEnv($serverName, $this->effectiveEnv($spec));
        } catch (\Throwable $e) {
            Log::error("Failed to update runtime env for server '{$serverName}': {$e->getMessage()}");
        }
    }

    public function baseUrl(): string
    {
        $hostname = (string) PlatformSetting::get('hostname', '');
        if ($hostname === '') {
            return (string) config('mcp.base_url');
        }
        $tls = PlatformSetting::get('tls_enabled', '') === 'true';
        $scheme = $tls ? 'https' : 'http';
        return "{$scheme}://{$hostname}";
    }
}
