<?php

namespace App\Services\Mcp;

final class EnvVar
{
    public function __construct(
        public readonly string $name,
        public readonly string $value,
    ) {}

    public static function fromArray(array $data): ?self
    {
        $name = trim((string) ($data['name'] ?? ''));
        if ($name === '') {
            return null;
        }
        $value = is_string($data['value'] ?? null) ? $data['value'] : '';
        return new self($name, $value);
    }

    public function toArray(): array
    {
        return ['name' => $this->name, 'value' => $this->value];
    }
}
