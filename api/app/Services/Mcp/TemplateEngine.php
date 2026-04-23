<?php

namespace App\Services\Mcp;

use Symfony\Component\Yaml\Yaml;
use Twig\Environment;
use Twig\Loader\FilesystemLoader;

/**
 * Port of app/template_engine.py — loads template bundles, renders *.j2 files
 * with user variables, copies other files to the build context.
 */
class TemplateEngine
{
    public function __construct(
        private readonly string $templatesDir,
        private readonly string $serversDir,
    ) {}

    /** @return array<int, array<string, mixed>> */
    public function listTemplates(): array
    {
        if (! is_dir($this->templatesDir)) {
            return [];
        }
        $out = [];
        $entries = scandir($this->templatesDir) ?: [];
        sort($entries);
        foreach ($entries as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $metaPath = $this->templatesDir."/{$entry}/template.yaml";
            if (is_file($metaPath)) {
                $meta = $this->loadMeta($metaPath);
                if ($meta) {
                    $out[] = $meta;
                }
            }
        }
        return $out;
    }

    public function getTemplate(string $name): ?array
    {
        $metaPath = $this->templatesDir."/{$name}/template.yaml";
        if (! is_file($metaPath)) {
            return null;
        }
        return $this->loadMeta($metaPath);
    }

    /**
     * Render a template bundle into the server's build-context directory.
     * Returns the absolute output directory path.
     *
     * @param array<string, string> $config
     */
    public function render(string $templateName, string $serverName, array $config): string
    {
        $templateDir = $this->templatesDir."/{$templateName}";
        if (! is_dir($templateDir)) {
            throw new \InvalidArgumentException("Template '{$templateName}' not found");
        }

        $meta = $this->loadMeta($templateDir.'/template.yaml');
        if (! $meta) {
            throw new \InvalidArgumentException("Invalid template metadata for '{$templateName}'");
        }

        $variables = ['server_name' => $serverName];
        foreach ($meta['variables'] as $var) {
            if (array_key_exists($var['name'], $config)) {
                $variables[$var['name']] = $config[$var['name']];
            } elseif ($var['default'] !== null) {
                $variables[$var['name']] = $var['default'];
            } elseif ($var['required']) {
                throw new \InvalidArgumentException("Required variable '{$var['name']}' not provided");
            }
        }

        $outputDir = $this->serversDir."/{$serverName}";
        if (! is_dir($outputDir)) {
            mkdir($outputDir, 0755, true);
        }

        $twig = new Environment(new FilesystemLoader($templateDir), [
            'autoescape' => false,
            'strict_variables' => false,
        ]);

        foreach (glob($templateDir.'/*.j2') as $j2File) {
            $name = basename($j2File);
            $rendered = $twig->render($name, $variables);
            $outName = substr($name, 0, -3); // strip .j2
            file_put_contents($outputDir.'/'.$outName, $rendered);
        }

        foreach (scandir($templateDir) as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $src = $templateDir.'/'.$entry;
            if (! is_file($src)) {
                continue;
            }
            $ext = pathinfo($entry, PATHINFO_EXTENSION);
            if ($ext === 'j2' || $ext === 'yaml') {
                continue;
            }
            copy($src, $outputDir.'/'.$entry);
        }

        return $outputDir;
    }

    public function cleanup(string $serverName): void
    {
        $dir = $this->serversDir."/{$serverName}";
        if (is_dir($dir)) {
            $this->rrmdir($dir);
        }
    }

    private function loadMeta(string $path): ?array
    {
        try {
            $data = Yaml::parseFile($path);
        } catch (\Throwable) {
            return null;
        }
        if (! is_array($data) || empty($data['name'])) {
            return null;
        }

        $vars = [];
        foreach ($data['variables'] ?? [] as $v) {
            if (! is_array($v) || empty($v['name'])) {
                continue;
            }
            $vars[] = [
                'name' => $v['name'],
                'description' => $v['description'] ?? '',
                'default' => array_key_exists('default', $v) ? (string) $v['default'] : null,
                'required' => (bool) ($v['required'] ?? false),
            ];
        }

        return [
            'name' => $data['name'],
            'description' => $data['description'] ?? '',
            'variables' => $vars,
        ];
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
