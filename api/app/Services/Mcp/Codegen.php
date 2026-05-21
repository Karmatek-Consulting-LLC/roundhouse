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
        // Auth is opt-in per server: enabled iff at least one token exists.
        // Without tokens, primitive-level require_scopes() would have no verifier
        // and the server is published unauthenticated.
        $authEnabled = ! empty($spec->tokens);
        $anyScopedPrimitive = $authEnabled && $this->anyPrimitiveHasScopes($spec->primitives);

        $primitives = [];
        foreach ($spec->primitives as $p) {
            $primitives[] = $this->generatePrimitive($p, $authEnabled);
        }
        $primitivesCode = trim(implode("\n", $primitives));

        $importLines = [];
        $importLines[] = 'from fastmcp import FastMCP';
        $authImports = [];
        if ($authEnabled) {
            $authImports[] = 'StaticTokenVerifier';
        }
        if ($anyScopedPrimitive) {
            $authImports[] = 'require_scopes';
        }
        if ($authImports) {
            $importLines[] = 'from fastmcp.server.auth import '.implode(', ', $authImports);
        }
        foreach ($spec->imports as $extra) {
            if ($extra !== '') {
                $importLines[] = $extra;
            }
        }

        $mcpArgs = [$this->pyString($spec->name)];
        if ($authEnabled) {
            $mcpArgs[] = 'auth=StaticTokenVerifier(tokens='.$this->pyDict($this->tokensMap($spec->tokens)).')';
        }

        $lines = $importLines;
        $lines[] = '';
        $lines[] = 'mcp = FastMCP('.implode(', ', $mcpArgs).')';
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

    /** @param array<int, array<string, mixed>> $primitives */
    private function anyPrimitiveHasScopes(array $primitives): bool
    {
        foreach ($primitives as $p) {
            if (! empty($p['scopes']) && is_array($p['scopes'])) {
                return true;
            }
        }
        return false;
    }

    /**
     * Build the StaticTokenVerifier tokens map.
     * Shape: { "<plaintext-token>": {"client_id": "<token name>", "scopes": [...]} }
     *
     * @param array<int, array{name:string, token:string, scopes:string[]}> $tokens
     * @return array<string, array{client_id:string, scopes:string[]}>
     */
    private function tokensMap(array $tokens): array
    {
        $map = [];
        foreach ($tokens as $t) {
            $plain = (string) ($t['token'] ?? '');
            if ($plain === '') {
                continue;
            }
            $map[$plain] = [
                'client_id' => (string) ($t['name'] ?? ''),
                'scopes' => array_values(array_filter(
                    (array) ($t['scopes'] ?? []),
                    fn ($s) => is_string($s) && $s !== '',
                )),
            ];
        }
        return $map;
    }

    /**
     * fastmcp version pinned to a known-good release that exposes
     * StaticTokenVerifier and require_scopes at fastmcp.server.auth.
     * Bump deliberately - generated server.py is coupled to this API surface.
     */
    public const FASTMCP_VERSION = '3.3.1';

    /**
     * @param ?string $customCa  PEM bundle to install as a trusted root before
     *                           any network call. When non-null, the Dockerfile
     *                           expects a sibling `custom-ca.crt` in the build
     *                           context (writeBuildContext handles this).
     */
    public function generateDockerfile(ServerSpec $spec, ?string $customCa = null): string
    {
        $lines = [
            'FROM python:3.12-slim',
            'WORKDIR /app',
        ];

        if ($this->hasCustomCa($customCa)) {
            // Append the corp CA to the existing trust bundle BEFORE any
            // network call. python:3.12-slim inherits ca-certificates from
            // debian-slim, so /etc/ssl/certs/ca-certificates.crt already
            // exists - we just edit it. This avoids the chicken-and-egg of
            // needing TLS to apt-install ca-certificates to enable TLS.
            $lines[] = 'COPY custom-ca.crt /usr/local/share/ca-certificates/custom-ca.crt';
            $lines[] = 'RUN cat /usr/local/share/ca-certificates/custom-ca.crt >> /etc/ssl/certs/ca-certificates.crt \\';
            $lines[] = '    && update-ca-certificates';
            $lines[] = 'ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \\';
            $lines[] = '    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \\';
            $lines[] = '    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt';
        }

        if ($spec->aptPackages) {
            $apt = implode(' ', $spec->aptPackages);
            $lines[] = "RUN apt-get update && apt-get install -y --no-install-recommends {$apt} && rm -rf /var/lib/apt/lists/*";
        }

        $pipInstall = 'fastmcp=='.self::FASTMCP_VERSION;
        if ($spec->pipPackages) {
            $pipInstall .= ' '.implode(' ', $spec->pipPackages);
        }
        $lines[] = "RUN pip install --no-cache-dir {$pipInstall}";
        $lines[] = 'COPY server.py .';
        $lines[] = 'EXPOSE 8000';
        $lines[] = 'CMD ["python", "server.py"]';
        $lines[] = '';
        return implode("\n", $lines);
    }

    /**
     * Write server.py, Dockerfile, and optionally custom-ca.crt into a
     * build-context directory. In code mode, the user's source is written
     * verbatim (no codegen).
     */
    public function writeBuildContext(ServerSpec $spec, string $outputDir, ?string $customCa = null): string
    {
        if (! is_dir($outputDir)) {
            mkdir($outputDir, 0755, true);
        }
        $serverPy = $spec->isCodeMode()
            ? (string) ($spec->source ?? '')
            : $this->generateServerPy($spec);

        file_put_contents($outputDir.'/server.py', $serverPy);
        file_put_contents($outputDir.'/Dockerfile', $this->generateDockerfile($spec, $customCa));

        $caPath = $outputDir.'/custom-ca.crt';
        if ($this->hasCustomCa($customCa)) {
            file_put_contents($caPath, $customCa);
        } else {
            // Drop stale CA from a prior build so the Dockerfile and context stay in sync.
            @unlink($caPath);
        }

        return $outputDir;
    }

    private function hasCustomCa(?string $ca): bool
    {
        return $ca !== null && trim($ca) !== '';
    }

    /** @param array<string, mixed> $p */
    private function generatePrimitive(array $p, bool $authEnabled): string
    {
        return match ($p['kind'] ?? '') {
            'tool' => $this->generateTool($p, $authEnabled),
            'resource' => $this->generateResource($p, $authEnabled),
            'resource_template' => $this->generateResourceTemplate($p, $authEnabled),
            'prompt' => $this->generatePrompt($p, $authEnabled),
            default => '',
        };
    }

    /**
     * Decorator args for primitive-level scope enforcement.
     * Returns '' when auth is disabled or the primitive has no scopes;
     * otherwise 'auth=require_scopes("a", "b")'.
     *
     * @param array<string, mixed> $p
     */
    private function authClause(array $p, bool $authEnabled): string
    {
        if (! $authEnabled || empty($p['scopes']) || ! is_array($p['scopes'])) {
            return '';
        }
        $scopeArgs = [];
        foreach ($p['scopes'] as $s) {
            if (is_string($s) && $s !== '') {
                $scopeArgs[] = $this->pyString($s);
            }
        }
        if (! $scopeArgs) {
            return '';
        }
        return 'auth=require_scopes('.implode(', ', $scopeArgs).')';
    }

    /** Join leading-positional decorator args with an optional auth= clause. */
    private function decoratorArgs(string $positional, string $auth): string
    {
        $parts = [];
        if ($positional !== '') {
            $parts[] = $positional;
        }
        if ($auth !== '') {
            $parts[] = $auth;
        }
        return implode(', ', $parts);
    }

    private function generateTool(array $t, bool $authEnabled): string
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
        $args = $this->decoratorArgs('', $this->authClause($t, $authEnabled));
        return "\n@mcp.tool({$args})\ndef {$name}({$sig}) -> {$retPy}:\n    {$doc}\n".$this->indent($body)."\n";
    }

    private function generateResource(array $r, bool $authEnabled): string
    {
        $body = trim((string) ($r['code'] ?? ''));
        if ($body === '') {
            $body = 'return "'.($r['name'] ?? '').'"';
        }
        $doc = $this->pyString(($r['description'] ?? '') ?: ($r['name'] ?? ''));
        $uri = $this->pyString((string) ($r['uri'] ?? ''));
        $name = $r['name'] ?? '';
        $args = $this->decoratorArgs($uri, $this->authClause($r, $authEnabled));
        return "\n@mcp.resource({$args})\ndef {$name}() -> str:\n    {$doc}\n".$this->indent($body)."\n";
    }

    private function generateResourceTemplate(array $rt, bool $authEnabled): string
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
        $args = $this->decoratorArgs($uri, $this->authClause($rt, $authEnabled));
        return "\n@mcp.resource({$args})\ndef {$name}({$sig}) -> str:\n    {$doc}\n".$this->indent($body)."\n";
    }

    private function generatePrompt(array $p, bool $authEnabled): string
    {
        $sig = $this->paramSignature($p['parameters'] ?? []);
        $body = trim((string) ($p['code'] ?? ''));
        if ($body === '') {
            $body = 'return "Not implemented"';
        }
        $doc = $this->pyString(($p['description'] ?? '') ?: ($p['name'] ?? ''));
        $name = $p['name'] ?? '';
        $args = $this->decoratorArgs('', $this->authClause($p, $authEnabled));
        return "\n@mcp.prompt({$args})\ndef {$name}({$sig}) -> str:\n    {$doc}\n".$this->indent($body)."\n";
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

    /**
     * Produce a Python dict literal from a PHP array containing only strings,
     * ints, and nested arrays of the same. JSON is a valid Python literal for
     * this shape (we don't emit bools/null in the auth tokens map).
     *
     * Relies on PHP's natural list-vs-object inference: sequential int keys
     * become JSON arrays (Python lists), string keys become JSON objects
     * (Python dicts). Do NOT add JSON_FORCE_OBJECT here - it would turn nested
     * scopes lists into dicts like {"0":"read"} and break AccessToken
     * validation at runtime.
     *
     * @param array<mixed, mixed> $a
     */
    private function pyDict(array $a): string
    {
        return json_encode($a, JSON_UNESCAPED_SLASHES);
    }

    private function indent(string $code, int $level = 1): string
    {
        $prefix = str_repeat('    ', $level);
        $lines = explode("\n", rtrim($code));
        return implode("\n", array_map(fn ($l) => trim($l) === '' ? '' : $prefix.$l, $lines));
    }
}
