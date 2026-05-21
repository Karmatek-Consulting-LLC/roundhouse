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

test('Dockerfile lists pip packages alongside pinned fastmcp', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'demo',
        'pip_packages' => ['requests', 'httpx'],
    ]);
    $df = $cg->generateDockerfile($spec);
    expect($df)->toContain('RUN pip install --no-cache-dir fastmcp=='.Codegen::FASTMCP_VERSION.' requests httpx');
    expect($df)->toContain('CMD ["python", "server.py"]');
});

test('no auth: no auth imports and no auth= arg on FastMCP', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'plain',
        'primitives' => [[
            'kind' => 'tool',
            'name' => 'hello',
            'description' => 'Hello',
            'code' => 'return "ok"',
        ]],
    ]);
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'no auth');
    expect($py)->not->toContain('StaticTokenVerifier');
    expect($py)->not->toContain('require_scopes');
    expect($py)->toContain('mcp = FastMCP("plain")');
    expect($py)->toContain('@mcp.tool()');
});

test('tokens only: StaticTokenVerifier imported, no require_scopes when no primitive scopes', function () {
    $cg = new Codegen();
    $spec = new ServerSpec(
        name: 'guarded',
        primitives: [[
            'kind' => 'tool', 'name' => 'hello', 'description' => 'd', 'code' => 'return "ok"',
        ]],
        tokens: [
            ['name' => 'CI', 'token' => 'mcps_aaa', 'scopes' => []],
        ],
    );
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'tokens only');
    expect($py)->toContain('from fastmcp.server.auth import StaticTokenVerifier');
    expect($py)->not->toContain('require_scopes');
    expect($py)->toContain('auth=StaticTokenVerifier(tokens=');
    expect($py)->toContain('"mcps_aaa"');
    expect($py)->toContain('"client_id":"CI"');
    expect($py)->toContain('@mcp.tool()');
});

test('scoped primitive but no tokens: scopes are dropped from decorator (auth opt-in)', function () {
    $cg = new Codegen();
    $spec = ServerSpec::fromArray([
        'name' => 'opt-in',
        'primitives' => [[
            'kind' => 'tool',
            'name' => 'hello',
            'description' => 'd',
            'code' => 'return "ok"',
            'scopes' => ['read'],
        ]],
    ]);
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'no-token scoped');
    expect($py)->not->toContain('require_scopes');
    expect($py)->not->toContain('StaticTokenVerifier');
    expect($py)->toContain('@mcp.tool()');
});

test('tokens + scoped primitive: both imports and decorator scope check emitted', function () {
    $cg = new Codegen();
    $spec = new ServerSpec(
        name: 'full',
        primitives: [[
            'kind' => 'tool', 'name' => 'read_thing', 'description' => 'd',
            'code' => 'return "ok"', 'scopes' => ['read', 'admin'],
        ]],
        tokens: [
            ['name' => 'CI', 'token' => 'mcps_bbb', 'scopes' => ['read']],
        ],
    );
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'full auth');
    expect($py)->toContain('from fastmcp.server.auth import StaticTokenVerifier, require_scopes');
    expect($py)->toContain('auth=StaticTokenVerifier(');
    expect($py)->toContain('@mcp.tool(auth=require_scopes("read", "admin"))');
});

test('token scopes serialize as a JSON list, not a dict (FastMCP AccessToken contract)', function () {
    // Regression: pyDict() used to emit JSON_FORCE_OBJECT which made nested
    // scopes render as {"0":"read"} and crashed FastMCP's AccessToken validator
    // at runtime (passes ast.parse but fails pydantic at request time).
    $cg = new Codegen();
    $spec = new ServerSpec(
        name: 'regression',
        primitives: [],
        tokens: [['name' => 'CI', 'token' => 'mcps_xxx', 'scopes' => ['read', 'write']]],
    );
    $py = $cg->generateServerPy($spec);
    expect($py)->toContain('"scopes":["read","write"]');
    expect($py)->not->toContain('"scopes":{');
});

test('scopes propagate into resource, resource_template, prompt decorators', function () {
    $cg = new Codegen();
    $spec = new ServerSpec(
        name: 'all-kinds',
        primitives: [
            [
                'kind' => 'resource', 'name' => 'doc', 'uri' => '/doc',
                'description' => 'd', 'code' => 'return "x"', 'scopes' => ['read'],
            ],
            [
                'kind' => 'resource_template', 'name' => 'item',
                'uri_template' => '/items/{id}', 'description' => 'd',
                'code' => 'return f"{id}"', 'scopes' => ['read'],
            ],
            [
                'kind' => 'prompt', 'name' => 'greet',
                'description' => 'd', 'code' => 'return "hi"', 'scopes' => ['read'],
            ],
        ],
        tokens: [['name' => 'CI', 'token' => 'mcps_ccc', 'scopes' => ['read']]],
    );
    $py = $cg->generateServerPy($spec);
    assertValidPython($py, 'all kinds scoped');
    expect($py)->toContain('@mcp.resource("/doc", auth=require_scopes("read"))');
    expect($py)->toContain('@mcp.resource("/items/{id}", auth=require_scopes("read"))');
    expect($py)->toContain('@mcp.prompt(auth=require_scopes("read"))');
});
