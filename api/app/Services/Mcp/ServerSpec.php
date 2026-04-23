<?php

namespace App\Services\Mcp;

/**
 * Persisted server definition — mirrors platform/app/models.py::ServerSpec.
 * Primitives are kept as associative arrays (with a `kind` discriminator) to
 * match the Python JSON schema exactly.
 */
final class ServerSpec
{
    /**
     * @param array<int, array<string, mixed>> $primitives  tool | resource | resource_template | prompt
     * @param string[] $imports
     * @param string[] $pipPackages
     * @param string[] $envGlobalImports
     * @param EnvVar[] $envVars
     */
    public function __construct(
        public string $name,
        public string $description = '',
        public array $imports = [],
        public array $primitives = [],
        public array $pipPackages = [],
        public array $envGlobalImports = [],
        public array $envVars = [],
        public ?int $replicas = null,
    ) {}

    public static function fromArray(array $data): self
    {
        $envVars = [];
        foreach ($data['env_vars'] ?? [] as $ev) {
            if (is_array($ev)) {
                $parsed = EnvVar::fromArray($ev);
                if ($parsed) {
                    $envVars[] = $parsed;
                }
            }
        }

        $replicas = $data['replicas'] ?? null;
        if ($replicas !== null) {
            $replicas = (int) $replicas;
        }

        return new self(
            name: (string) ($data['name'] ?? ''),
            description: (string) ($data['description'] ?? ''),
            imports: self::stringList($data['imports'] ?? []),
            primitives: self::primitiveList($data['primitives'] ?? []),
            pipPackages: self::stringList($data['pip_packages'] ?? []),
            envGlobalImports: self::normalizeEnvImports($data['env_global_imports'] ?? []),
            envVars: $envVars,
            replicas: $replicas,
        );
    }

    public function toArray(): array
    {
        return [
            'name' => $this->name,
            'description' => $this->description,
            'imports' => $this->imports,
            'primitives' => $this->primitives,
            'pip_packages' => $this->pipPackages,
            'env_global_imports' => $this->envGlobalImports,
            'env_vars' => array_map(fn (EnvVar $v) => $v->toArray(), $this->envVars),
            'replicas' => $this->replicas,
        ];
    }

    /** @param array<int, mixed> $v @return string[] */
    public static function normalizeEnvImports(array $v): array
    {
        $seen = [];
        $out = [];
        foreach ($v as $item) {
            if (! is_string($item)) {
                continue;
            }
            $name = self::normalizeEnvName($item);
            if ($name !== '' && ! isset($seen[$name])) {
                $seen[$name] = true;
                $out[] = $name;
            }
        }
        return $out;
    }

    public static function normalizeEnvName(string $n): string
    {
        $s = strtoupper(trim($n));
        return preg_replace('/[^A-Z0-9_]/', '', $s) ?? '';
    }

    /** @return string[] */
    private static function stringList(array $items): array
    {
        $out = [];
        foreach ($items as $i) {
            if (is_string($i)) {
                $out[] = $i;
            }
        }
        return $out;
    }

    /** @return array<int, array<string, mixed>> */
    private static function primitiveList(array $items): array
    {
        $out = [];
        foreach ($items as $p) {
            if (is_array($p) && isset($p['kind'])) {
                $out[] = $p;
            }
        }
        return $out;
    }
}
