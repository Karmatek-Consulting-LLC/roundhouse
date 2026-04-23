<?php

namespace App\Services\Mcp;

/**
 * Generate Python FastMCP server.py and Dockerfile from a ServerSpec.
 * Port of platform/app/codegen.py.
 */
class Codegen
{
    private const PYTHON_TYPE_MAP = [
        'str' => 'str',
        'int' => 'int',
        'float' => 'float',
        'bool' => 'bool',
        'list' => 'list',
        'dict' => 'dict',
    ];

    public function generateServerPy(ServerSpec $spec): string
    {
        $primitives = [];
        foreach ($spec->primitives as $p) {
            $primitives[] = $this->generatePrimitive($p);
        }
        $primitivesCode = trim(implode("\n", $primitives));

        $importLines = implode("\n", $spec->imports);

        $lines = ['from fastmcp import FastMCP'];
        if ($importLines !== '') {
            $lines[] = $importLines;
        }
        $lines[] = '';
        $lines[] = 'mcp = FastMCP('.$this->pyString($spec->name).')';
        $lines[] = '';
        $lines[] = $primitivesCode;
        $lines[] = '';
        $lines[] = 'if __name__ == "__main__":';
        $lines[] = '    mcp.run(';
        $lines[] = '        transport="streamable-http",';
        $lines[] = '        host="0.0.0.0",';
        $lines[] = '        port=8000,';
        $lines[] = '        stateless_http=True,';
        $lines[] = '        json_response=True,';
        $lines[] = '    )';
        $lines[] = '';
        return implode("\n", $lines);
    }

    public function generateDockerfile(ServerSpec $spec): string
    {
        $pipInstall = 'fastmcp';
        if ($spec->pipPackages) {
            $pipInstall .= ' '.implode(' ', $spec->pipPackages);
        }
        $lines = [
            'FROM python:3.12-slim',
            'WORKDIR /app',
            "RUN pip install --no-cache-dir {$pipInstall}",
            'COPY server.py .',
            'EXPOSE 8000',
            'CMD ["python", "server.py"]',
            '',
        ];
        return implode("\n", $lines);
    }

    /** Write server.py and Dockerfile into a build-context directory. */
    public function writeBuildContext(ServerSpec $spec, string $outputDir): string
    {
        if (! is_dir($outputDir)) {
            mkdir($outputDir, 0755, true);
        }
        file_put_contents($outputDir.'/server.py', $this->generateServerPy($spec));
        file_put_contents($outputDir.'/Dockerfile', $this->generateDockerfile($spec));
        return $outputDir;
    }

    /** @param array<string, mixed> $p */
    private function generatePrimitive(array $p): string
    {
        return match ($p['kind'] ?? '') {
            'tool' => $this->generateTool($p),
            'resource' => $this->generateResource($p),
            'resource_template' => $this->generateResourceTemplate($p),
            'prompt' => $this->generatePrompt($p),
            default => '',
        };
    }

    private function generateTool(array $t): string
    {
        $sig = $this->paramSignature($t['parameters'] ?? []);
        $retPy = ($t['return_type'] ?? 'str') === 'dict' ? 'dict' : 'str';
        $defaultBody = $retPy === 'str' ? 'return "Not implemented"' : 'return {}';
        $body = trim((string) ($t['code'] ?? ''));
        if ($body === '') {
            $body = $defaultBody;
        }
        $doc = $this->pyString(($t['description'] ?? '') ?: ($t['name'] ?? ''));
        $name = $t['name'] ?? '';
        return "\n@mcp.tool()\ndef {$name}({$sig}) -> {$retPy}:\n    {$doc}\n".$this->indent($body)."\n";
    }

    private function generateResource(array $r): string
    {
        $body = trim((string) ($r['code'] ?? ''));
        if ($body === '') {
            $body = 'return "'.($r['name'] ?? '').'"';
        }
        $doc = $this->pyString(($r['description'] ?? '') ?: ($r['name'] ?? ''));
        $uri = $this->pyString((string) ($r['uri'] ?? ''));
        $name = $r['name'] ?? '';
        return "\n@mcp.resource({$uri})\ndef {$name}() -> str:\n    {$doc}\n".$this->indent($body)."\n";
    }

    private function generateResourceTemplate(array $rt): string
    {
        $template = (string) ($rt['uri_template'] ?? '');
        preg_match_all('/\{(\w+)\}/', $template, $m);
        $params = $m[1] ?? [];
        $sig = implode(', ', array_map(fn ($p) => "{$p}: str", $params));

        $body = trim((string) ($rt['code'] ?? ''));
        if ($body === '') {
            $body = 'return "'.($rt['name'] ?? '').'"';
        }
        $doc = $this->pyString(($rt['description'] ?? '') ?: ($rt['name'] ?? ''));
        $uri = $this->pyString($template);
        $name = $rt['name'] ?? '';
        return "\n@mcp.resource({$uri})\ndef {$name}({$sig}) -> str:\n    {$doc}\n".$this->indent($body)."\n";
    }

    private function generatePrompt(array $p): string
    {
        $sig = $this->paramSignature($p['parameters'] ?? []);
        $body = trim((string) ($p['code'] ?? ''));
        if ($body === '') {
            $body = 'return "Not implemented"';
        }
        $doc = $this->pyString(($p['description'] ?? '') ?: ($p['name'] ?? ''));
        $name = $p['name'] ?? '';
        return "\n@mcp.prompt()\ndef {$name}({$sig}) -> str:\n    {$doc}\n".$this->indent($body)."\n";
    }

    /** @param array<int, array<string, mixed>> $params */
    private function paramSignature(array $params): string
    {
        $required = [];
        $optional = [];
        foreach ($params as $p) {
            if (! is_array($p) || empty($p['name'])) {
                continue;
            }
            $type = self::PYTHON_TYPE_MAP[$p['type'] ?? 'str'] ?? 'str';
            if ($p['required'] ?? true) {
                $required[] = "{$p['name']}: {$type}";
            } else {
                $default = $p['default'] ?? null;
                $defaultRepr = $default === null ? 'None' : $this->pyString((string) $default);
                $optional[] = "{$p['name']}: {$type} = {$defaultRepr}";
            }
        }
        return implode(', ', [...$required, ...$optional]);
    }

    /** Produce a safe Python string literal. JSON encoding is a valid Python string. */
    private function pyString(string $s): string
    {
        return json_encode($s, JSON_UNESCAPED_SLASHES);
    }

    private function indent(string $code, int $level = 1): string
    {
        $prefix = str_repeat('    ', $level);
        $lines = explode("\n", rtrim($code));
        return implode("\n", array_map(fn ($l) => trim($l) === '' ? '' : $prefix.$l, $lines));
    }
}
