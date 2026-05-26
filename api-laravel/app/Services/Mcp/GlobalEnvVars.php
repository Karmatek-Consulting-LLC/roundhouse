<?php

namespace App\Services\Mcp;

use App\Models\PlatformSetting;
use Illuminate\Support\Facades\Log;

/**
 * Global MCP env vars stored as JSON in platform_settings.
 * Mirrors app/mcp_env.py in the Python platform.
 */
class GlobalEnvVars
{
    public const SETTING_KEY = 'mcp_global_env_vars';

    /** @return EnvVar[] */
    public function all(): array
    {
        $raw = trim((string) PlatformSetting::get(self::SETTING_KEY, ''));
        if ($raw === '') {
            return [];
        }

        try {
            $data = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            Log::warning('Invalid JSON for '.self::SETTING_KEY.'; treating as empty');
            return [];
        }

        if (! is_array($data)) {
            return [];
        }

        $out = [];
        foreach ($data as $item) {
            if (! is_array($item)) {
                continue;
            }
            $ev = EnvVar::fromArray($item);
            if ($ev) {
                $out[] = $ev;
            }
        }
        return $out;
    }

    /** @param EnvVar[] $vars */
    public function save(array $vars): void
    {
        $payload = array_map(fn (EnvVar $v) => $v->toArray(), $vars);
        PlatformSetting::put(self::SETTING_KEY, json_encode($payload));
    }

    /** @return array<string, string> */
    public function asDict(): array
    {
        $out = [];
        foreach ($this->all() as $ev) {
            $out[$ev->name] = $ev->value;
        }
        return $out;
    }
}
