<?php

use App\Models\ServerOwner;
use App\Models\User;
use App\Services\Mcp\DockerClient;
use App\Services\Mcp\McpClient;
use App\Services\Mcp\McpException;
use Illuminate\Support\Facades\Hash;

beforeEach(function () {
    $this->admin = User::query()->create([
        'email' => 'admin@test.local',
        'password_hash' => Hash::make('pw'),
        'display_name' => 'Admin',
        'role' => 'superadmin',
    ]);
    $this->token = $this->admin->createToken('t')->plainTextToken;

    ServerOwner::query()->create([
        'server_name' => 'demo',
        'owner_id' => $this->admin->id,
    ]);

    $this->docker = mock(DockerClient::class);
    $this->docker->shouldReceive('getServer')->with('demo')->andReturn(['name' => 'demo'])->byDefault();
    $this->app->instance(DockerClient::class, $this->docker);

    $this->mcp = mock(McpClient::class);
    $this->app->instance(McpClient::class, $this->mcp);
});

function authHeader(): array
{
    return ['Authorization' => 'Bearer '.test()->token];
}

test('GET /servers/{name}/tools returns tool list from MCP', function () {
    $this->mcp->shouldReceive('listTools')->with('demo')->andReturn([
        ['name' => 'greet', 'description' => 'say hi'],
    ]);

    $this->withHeaders(authHeader())->getJson('/api/servers/demo/tools')
        ->assertOk()
        ->assertJson(['tools' => [['name' => 'greet']]]);
});

test('POST /servers/{name}/tools/invoke passes through tool + arguments', function () {
    $this->mcp->shouldReceive('callTool')
        ->with('demo', 'greet', ['name' => 'Marty'])
        ->andReturn(['content' => [['type' => 'text', 'text' => 'Hello, Marty']]]);

    $this->withHeaders(authHeader())
        ->postJson('/api/servers/demo/tools/invoke', [
            'tool' => 'greet',
            'arguments' => ['name' => 'Marty'],
        ])
        ->assertOk()
        ->assertJsonPath('content.0.text', 'Hello, Marty');
});

test('POST /servers/{name}/resources/read pipes through URI', function () {
    $this->mcp->shouldReceive('readResource')
        ->with('demo', 'file://foo')
        ->andReturn(['contents' => [['uri' => 'file://foo', 'text' => 'body']]]);

    $this->withHeaders(authHeader())
        ->postJson('/api/servers/demo/resources/read', ['uri' => 'file://foo'])
        ->assertOk()
        ->assertJsonPath('contents.0.text', 'body');
});

test('POST /servers/{name}/prompts/get pipes through name + args', function () {
    $this->mcp->shouldReceive('getPrompt')
        ->with('demo', 'summarize', ['topic' => 'MCP'])
        ->andReturn(['messages' => [['role' => 'user', 'content' => 'Summarize MCP']]]);

    $this->withHeaders(authHeader())
        ->postJson('/api/servers/demo/prompts/get', [
            'prompt' => 'summarize',
            'arguments' => ['topic' => 'MCP'],
        ])
        ->assertOk()
        ->assertJsonPath('messages.0.content', 'Summarize MCP');
});

test('McpException bubbles out as 502', function () {
    $this->mcp->shouldReceive('callTool')
        ->andThrow(new McpException('MCP error -1: boom'));

    $this->withHeaders(authHeader())
        ->postJson('/api/servers/demo/tools/invoke', ['tool' => 'x'])
        ->assertStatus(502)
        ->assertJsonPath('detail', 'MCP error -1: boom');
});

test('not-deployed server returns 409', function () {
    $this->docker->shouldReceive('getServer')->with('demo')->andReturn(null);

    $this->withHeaders(authHeader())
        ->postJson('/api/servers/demo/tools/invoke', ['tool' => 'x'])
        ->assertStatus(409);
});

test('non-owning, non-teammate user gets 403', function () {
    $outsider = User::query()->create([
        'email' => 'carol@test.local',
        'password_hash' => Hash::make('pw'),
        'display_name' => 'Carol',
        'role' => 'user',
    ]);
    $outsiderToken = $outsider->createToken('t')->plainTextToken;

    $this->withHeaders(['Authorization' => "Bearer {$outsiderToken}"])
        ->postJson('/api/servers/demo/tools/invoke', ['tool' => 'x'])
        ->assertStatus(403);
});
