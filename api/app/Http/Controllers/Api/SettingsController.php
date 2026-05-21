<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\PlatformSetting;
use App\Services\Mcp\DockerClient;
use App\Services\Mcp\EnvVar;
use App\Services\Mcp\GlobalEnvVars;
use App\Services\Mcp\ServerService;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

class SettingsController extends Controller
{
    public const SETTING_HOSTNAME = 'hostname';
    public const SETTING_EXTERNAL_HTTPS = 'external_https';
    public const SETTING_DOCKER_REGISTRY = 'docker_registry';
    public const SETTING_DOCKER_REGISTRY_USERNAME = 'docker_registry_username';
    public const SETTING_DOCKER_REGISTRY_PASSWORD = 'docker_registry_password';
    public const SETTING_CUSTOM_CA_CERT = 'custom_ca_cert';

    public function __construct(
        private readonly DockerClient $docker,
        private readonly GlobalEnvVars $globals,
        private readonly ServerService $servers,
    ) {}

    public function index(): JsonResponse
    {
        return response()->json([
            'hostname' => (string) PlatformSetting::get(self::SETTING_HOSTNAME, ''),
            // True when the public URL is HTTPS (TLS terminated upstream by
            // cluster ingress / frontend Traefik / whatever). Drives the
            // scheme in generated server URLs.
            'external_https' => PlatformSetting::get(self::SETTING_EXTERNAL_HTTPS, '') === 'true',
            'base_url' => $this->baseUrl(),
            'default_mcp_server_replicas' => (int) config('mcp.default_server_replicas'),
            'max_mcp_server_replicas' => (int) config('mcp.max_server_replicas'),
            'docker_swarm_mode' => $this->docker->swarmMode(),
            'docker_registry' => $this->dockerRegistryRaw(),
            'docker_registry_effective' => $this->dockerRegistryPrefix() ?? '',
            'docker_registry_username' => (string) PlatformSetting::get(self::SETTING_DOCKER_REGISTRY_USERNAME, ''),
            'docker_registry_password_configured' => $this->dockerRegistryPasswordConfigured(),
            // Don't return the PEM contents - just whether one is configured.
            // The cert is public by nature, but the index endpoint is for confirmation
            // not retrieval - keeps the payload small.
            'custom_ca_cert_configured' => trim((string) PlatformSetting::get(self::SETTING_CUSTOM_CA_CERT, '')) !== '',
        ]);
    }

    public function updateCustomCa(Request $request): JsonResponse
    {
        $data = $request->validate([
            'cert' => ['required', 'string', 'max:262144'],
        ]);
        // Permissive: store whatever bytes the user pastes. Build-time errors
        // surface clearly if the content isn't a valid PEM bundle.
        PlatformSetting::put(self::SETTING_CUSTOM_CA_CERT, $data['cert']);
        return response()->json(['custom_ca_cert_configured' => true]);
    }

    public function deleteCustomCa(): JsonResponse
    {
        PlatformSetting::put(self::SETTING_CUSTOM_CA_CERT, '');
        return response()->json(['custom_ca_cert_configured' => false]);
    }

    public function getMcpEnv(): JsonResponse
    {
        return response()->json([
            'env_vars' => array_map(fn (EnvVar $v) => $v->toArray(), $this->globals->all()),
        ]);
    }

    public function putMcpEnv(Request $request): JsonResponse
    {
        $data = $request->validate([
            'env_vars' => ['sometimes', 'array'],
            'env_vars.*.name' => ['required', 'string'],
            'env_vars.*.value' => ['sometimes', 'string'],
        ]);

        $vars = [];
        foreach ($data['env_vars'] ?? [] as $item) {
            $ev = EnvVar::fromArray($item);
            if ($ev) {
                $vars[] = $ev;
            }
        }
        $this->globals->save($vars);
        $this->servers->reapplyRuntimeEnvForAllServers();

        return $this->getMcpEnv();
    }

    public function updateHostname(Request $request): JsonResponse
    {
        $data = $request->validate([
            'hostname' => ['required', 'string'],
            // Optional - the UI saves scheme + hostname together so the
            // public base URL stays consistent.
            'external_https' => ['sometimes', 'boolean'],
        ]);
        $hostname = trim($data['hostname']);
        PlatformSetting::put(self::SETTING_HOSTNAME, $hostname);
        if (array_key_exists('external_https', $data)) {
            PlatformSetting::put(self::SETTING_EXTERNAL_HTTPS, $data['external_https'] ? 'true' : 'false');
        }

        return response()->json([
            'hostname' => $hostname,
            'external_https' => PlatformSetting::get(self::SETTING_EXTERNAL_HTTPS, '') === 'true',
            'base_url' => $this->baseUrl(),
        ]);
    }

    public function updateDockerRegistry(Request $request): JsonResponse
    {
        $data = $request->validate([
            'registry' => ['sometimes', 'string'],
            'username' => ['sometimes', 'string'],
            'password' => ['sometimes', 'nullable', 'string'],
        ]);

        $registry = trim($data['registry'] ?? '');
        PlatformSetting::put(self::SETTING_DOCKER_REGISTRY, $registry);
        PlatformSetting::put(self::SETTING_DOCKER_REGISTRY_USERNAME, trim($data['username'] ?? ''));

        if (array_key_exists('password', $data)) {
            PlatformSetting::put(self::SETTING_DOCKER_REGISTRY_PASSWORD, $data['password'] ?? '');
        }

        return response()->json([
            'docker_registry' => $registry,
            'docker_registry_effective' => $this->dockerRegistryPrefix() ?? '',
            'docker_registry_username' => (string) PlatformSetting::get(self::SETTING_DOCKER_REGISTRY_USERNAME, ''),
            'docker_registry_password_configured' => $this->dockerRegistryPasswordConfigured(),
        ]);
    }

    private function baseUrl(): string
    {
        $hostname = (string) PlatformSetting::get(self::SETTING_HOSTNAME, '');
        if ($hostname === '') {
            return (string) config('mcp.base_url');
        }
        $https = PlatformSetting::get(self::SETTING_EXTERNAL_HTTPS, '') === 'true';
        $scheme = $https ? 'https' : 'http';
        return "{$scheme}://{$hostname}";
    }

    private function dockerRegistryRaw(): string
    {
        return trim((string) PlatformSetting::get(self::SETTING_DOCKER_REGISTRY, ''));
    }

    private function dockerRegistryPrefix(): ?string
    {
        $raw = $this->dockerRegistryRaw();
        return $raw === '' ? null : rtrim($raw, '/');
    }

    private function dockerRegistryPasswordConfigured(): bool
    {
        return trim((string) PlatformSetting::get(self::SETTING_DOCKER_REGISTRY_PASSWORD, '')) !== '';
    }

}
