<?php

namespace App\Services\Mcp;

/**
 * JSON-file persistence for ServerSpecs - port of server_store.py.
 * Each server lives at {base_dir}/{name}/server.json
 */
class ServerStore
{
    public function __construct(private readonly string $baseDir) {}

    public function save(ServerSpec $spec): void
    {
        $dir = $this->serverDir($spec->name);
        if (! is_dir($dir)) {
            mkdir($dir, 0755, true);
        }
        $json = json_encode($spec->toArray(), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
        file_put_contents($this->specPath($spec->name), $json);
    }

    public function load(string $name): ?ServerSpec
    {
        $path = $this->specPath($name);
        if (! is_file($path)) {
            return null;
        }
        $raw = file_get_contents($path);
        if ($raw === false) {
            return null;
        }
        $data = json_decode($raw, true);
        if (! is_array($data)) {
            return null;
        }
        return ServerSpec::fromArray($data);
    }

    public function delete(string $name): void
    {
        $dir = $this->serverDir($name);
        if (is_dir($dir)) {
            $this->rrmdir($dir);
        }
    }

    /** @return ServerSpec[] */
    public function listAll(): array
    {
        if (! is_dir($this->baseDir)) {
            return [];
        }
        $out = [];
        $entries = scandir($this->baseDir) ?: [];
        sort($entries);
        foreach ($entries as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $spec = $this->load($entry);
            if ($spec) {
                $out[] = $spec;
            }
        }
        return $out;
    }

    public function serverDir(string $name): string
    {
        return $this->baseDir.'/'.$name;
    }

    private function specPath(string $name): string
    {
        return $this->serverDir($name).'/server.json';
    }

    private function rrmdir(string $dir): void
    {
        foreach (scandir($dir) as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $path = $dir.'/'.$entry;
            is_dir($path) ? $this->rrmdir($path) : @unlink($path);
        }
        @rmdir($dir);
    }
}
