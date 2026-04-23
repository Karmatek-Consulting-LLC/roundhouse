<?php

use App\Services\Mcp\Codegen;
use App\Services\Mcp\ServerSpec;

function assertValidPython(string $code, string $label = 'generated'): void
{
    $tmp = tempnam(sys_get_temp_dir(), 'codegen-').'.py';
    file_put_contents($tmp, $code);
    $cmd = sprintf('python3 -c %s 2>&1', escapeshellarg("import ast; ast.parse(open('{$tmp}').read())"));
    exec($cmd, $out, $code_ret);
    @unlink($tmp);
    expect($code_ret)->toBe(0, "Generated code for {$label} failed to parse:\n".implode("\n", $out)."\n\n{$code}");
}

test('tool with dict return type emits dict annotation and compiles', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'demo',
        'primitives' => [[
            'kind' => 'tool',
            'name' => 'get_data',
            'description' => 'Returns a dict',
            'parameters' => [],
            'code' => 'return {"a": 1}',
            'return_type' => 'dict',
        ]],
    ]);
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'dict-return tool');
    expect($py)->toContain('def get_data() -> dict:');
});

test('tool description with double quotes compiles', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'demo',
        'primitives' => [[
            'kind' => 'tool',
            'name' => 'hello',
            'description' => 'This tool returns "Hello, world!"',
            'parameters' => [],
            'code' => 'return "ok"',
        ]],
    ]);
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'double-quote description');
});

test('tool with typed required + optional params compiles', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'demo',
        'primitives' => [[
            'kind' => 'tool',
            'name' => 'mixed',
            'description' => 'Mixed params',
            'parameters' => [
                ['name' => 'a', 'type' => 'str', 'required' => true],
                ['name' => 'b', 'type' => 'int', 'required' => false, 'default' => '3'],
            ],
            'code' => 'return "ok"',
        ]],
    ]);
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'mixed params');
    expect($py)->toContain('def mixed(a: str, b: int = "3") -> str:');
});

test('resource_template extracts uri params into function signature', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'demo',
        'primitives' => [[
            'kind' => 'resource_template',
            'name' => 'user',
            'uri_template' => '/users/{id}/profile',
            'description' => 'User profile',
            'code' => 'return f"user {id}"',
        ]],
    ]);
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'resource template');
    expect($py)->toContain('def user(id: str) -> str:');
});

test('Dockerfile lists pip packages alongside fastmcp', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'demo',
        'pip_packages' => ['requests', 'httpx'],
    ]);
    $df = $cg->generateDockerfile($spec);
    expect($df)->toContain('RUN pip install --no-cache-dir fastmcp requests httpx');
    expect($df)->toContain('CMD ["python", "server.py"]');
});
