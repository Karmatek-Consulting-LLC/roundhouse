<?php

use App\Models\ServerOwner;
use App\Models\User;
use App\Services\Mcp\Codegen;
use App\Services\Mcp\DockerClient;
use App\Services\Mcp\ServerService;
use App\Services\Mcp\ServerSpec;
use App\Services\Mcp\ServerStore;
use Illuminate\Support\Facades\Hash;

beforeEach(function () {
    $this->admin = User::query()->create([
        'email' => 'admin@test.local',
        'password_hash' => Hash::make('pw'),
        'display_name' => 'Admin',
        'role' => 'superadmin',
    ]);
    $this->token = $this->admin->createToken('t')->plainTextToken;

    // Use a throwaway storage dir per test run
    $this->tempDir = sys_get_temp_dir().'/mcp-test-'.uniqid();
    $realStore = new ServerStore($this->tempDir);
    $this->app->instance(ServerStore::class, $realStore);

    // Re-create ServerService bound to our real store + a mock DockerClient.
    $this->docker = mock(DockerClient::class);
    $this->docker->shouldReceive('swarmMode')->andReturn(false)->byDefault();
    $this->docker->shouldReceive('getServer')->andReturn(null)->byDefault();
    $this->docker->shouldReceive('buildAndStart')->andReturn([
        'name' => 'demo',
        'template' => 'custom',
        'status' => 'running',
        'created_at' => '',
        'replicas_running' => 1,
        'placement' => [],
    ])->byDefault();
    $this->docker->shouldReceive('removeServer')->andReturn(true)->byDefault();
    $this->app->instance(DockerClient::class, $this->docker);

    $this->app->instance(ServerService::class, new ServerService(
        docker: $this->docker,
        codegen: $this->app->make(Codegen::class),
        store: $realStore,
        templates: $this->app->make(\App\Services\Mcp\TemplateEngine::class),
        globals: $this->app->make(\App\Services\Mcp\GlobalEnvVars::class),
        auth: new \App\Services\Mcp\ServerAuthService($realStore),
    ));
});

afterEach(function () {
    if (is_dir($this->tempDir)) {
        shell_exec('rm -rf '.escapeshellarg($this->tempDir));
    }
});

function codeAuth(): array
{
    return ['Authorization' => 'Bearer '.test()->token];
}

test('create code-mode server persists source verbatim to disk', function () {
    $source = "from fastmcp import FastMCP\n\nmcp = FastMCP(\"demo\")\n# users own tool\n@mcp.tool()\ndef custom(): return 42\n";

    $this->withHeaders(codeAuth())
        ->postJson('/api/servers', [
            'name' => 'demo',
            'mode' => 'code',
            'source' => $source,
            'description' => 'User brought their own code',
        ])
        ->assertStatus(201)
        ->assertJsonPath('mode', 'code')
        ->assertJsonPath('source', $source);

    // Source lands at storage/app/servers/demo/server.py exactly as provided.
    expect(file_get_contents($this->tempDir.'/demo/server.py'))->toBe($source);
});

test('create code mode without source returns 422', function () {
    $this->withHeaders(codeAuth())
        ->postJson('/api/servers', [
            'name' => 'empty',
            'mode' => 'code',
        ])
        ->assertStatus(422)
        ->assertJsonPath('detail', 'source is required when mode is "code"');
});

test('create code mode with template returns 422', function () {
    $this->withHeaders(codeAuth())
        ->postJson('/api/servers', [
            'name' => 'mixed',
            'mode' => 'code',
            'source' => 'from fastmcp import FastMCP',
            'template' => 'hello-world',
        ])
        ->assertStatus(422);
});

test('PUT /source updates code and redeploys', function () {
    ServerOwner::query()->create(['server_name' => 'demo', 'owner_id' => $this->admin->id]);
    $this->app->make(ServerStore::class)->save(new ServerSpec(
        name: 'demo',
        mode: 'code',
        source: 'original',
    ));

    $this->withHeaders(codeAuth())
        ->putJson('/api/servers/demo/source', ['source' => "new_source\n"])
        ->assertOk()
        ->assertJsonPath('source', "new_source\n");

    expect(file_get_contents($this->tempDir.'/demo/server.py'))->toBe("new_source\n");
});

test('PUT /source on a structured server returns 409', function () {
    ServerOwner::query()->create(['server_name' => 'demo', 'owner_id' => $this->admin->id]);
    $this->app->make(ServerStore::class)->save(new ServerSpec(name: 'demo'));

    $this->withHeaders(codeAuth())
        ->putJson('/api/servers/demo/source', ['source' => 'anything'])
        ->assertStatus(409);
});

test('POST /primitives on a code-mode server returns 409', function () {
    ServerOwner::query()->create(['server_name' => 'demo', 'owner_id' => $this->admin->id]);
    $this->app->make(ServerStore::class)->save(new ServerSpec(
        name: 'demo',
        mode: 'code',
        source: 'x',
    ));

    $this->withHeaders(codeAuth())
        ->postJson('/api/servers/demo/primitives', [
            'primitive' => ['kind' => 'tool', 'name' => 'foo'],
        ])
        ->assertStatus(409);
});

test('PUT /packages on a code-mode server is allowed', function () {
    ServerOwner::query()->create(['server_name' => 'demo', 'owner_id' => $this->admin->id]);
    $this->app->make(ServerStore::class)->save(new ServerSpec(
        name: 'demo',
        mode: 'code',
        source: 'x',
    ));

    $this->withHeaders(codeAuth())
        ->putJson('/api/servers/demo/packages', ['pip_packages' => ['requests']])
        ->assertOk();
});

test('PUT /env on a code-mode server is allowed', function () {
    ServerOwner::query()->create(['server_name' => 'demo', 'owner_id' => $this->admin->id]);
    $this->app->make(ServerStore::class)->save(new ServerSpec(
        name: 'demo',
        mode: 'code',
        source: 'x',
    ));

    $this->withHeaders(codeAuth())
        ->putJson('/api/servers/demo/env', [
            'env_global_imports' => ['FOO'],
            'env_vars' => [['name' => 'BAR', 'value' => 'baz']],
        ])
        ->assertOk();
});

test('Codegen writeBuildContext writes source verbatim in code mode', function () {
    $spec = new ServerSpec(
        name: 'demo',
        pipPackages: ['requests'],
        mode: 'code',
        source: "import foo\nmcp = ...",
    );
    $cg = new Codegen();
    $dir = $this->tempDir.'/test';
    $cg->writeBuildContext($spec, $dir);

    expect(file_get_contents($dir.'/server.py'))->toBe("import foo\nmcp = ...");
    // Dockerfile still generated from pipPackages.
    expect(file_get_contents($dir.'/Dockerfile'))->toContain('pip install --no-cache-dir fastmcp=='.Codegen::FASTMCP_VERSION.' requests');
});
