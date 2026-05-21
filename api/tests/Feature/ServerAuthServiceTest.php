<?php

use App\Models\ServerOwner;
use App\Models\ServerScope;
use App\Models\ServerToken;
use App\Models\User;
use App\Services\Mcp\ServerAuthService;
use App\Services\Mcp\ServerSpec;
use App\Services\Mcp\ServerStore;
use Illuminate\Support\Facades\Hash;

beforeEach(function () {
    $this->user = User::query()->create([
        'email' => 'owner@test.local',
        'password_hash' => Hash::make('pw'),
        'display_name' => 'Owner',
        'role' => 'user',
    ]);
    ServerOwner::query()->create([
        'server_name' => 'demo',
        'owner_id' => $this->user->id,
    ]);

    $this->baseDir = sys_get_temp_dir().'/auth-svc-'.bin2hex(random_bytes(4));
    mkdir($this->baseDir, 0755, true);
    $this->store = new ServerStore($this->baseDir);
    $this->svc = new ServerAuthService($this->store);
});

afterEach(function () {
    if (is_dir($this->baseDir)) {
        exec('rm -rf '.escapeshellarg($this->baseDir));
    }
});

test('mintToken returns prefixed plaintext, stores it encrypted, sets display_prefix', function () {
    $minted = $this->svc->mintToken('demo', 'CI', ['read']);
    $plain = $minted['plaintext'];

    expect($plain)->toStartWith('mcps_');
    expect(strlen($plain))->toBe(5 + 48); // prefix + 36 bytes base64url

    $row = ServerToken::where('server_name', 'demo')->first();
    expect($row->name)->toBe('CI');
    expect($row->token)->toBe($plain); // decrypted via the cast
    expect($row->display_prefix)->toBe(substr($plain, 0, 12));
    expect($row->scopes)->toBe(['read']);

    // Encrypted at rest: raw column is not equal to plaintext.
    $raw = \DB::table('server_tokens')->where('id', $row->id)->value('token');
    expect($raw)->not->toBe($plain);
});

test('mintToken marks the server as needing a rebuild', function () {
    expect(ServerOwner::find('demo')->auth_rebuild_required_at)->toBeNull();
    $this->svc->mintToken('demo', 'CI', []);
    expect(ServerOwner::find('demo')->auth_rebuild_required_at)->not->toBeNull();
});

test('clearRebuildRequired resets the flag', function () {
    $this->svc->mintToken('demo', 'CI', []);
    $this->svc->clearRebuildRequired('demo');
    expect(ServerOwner::find('demo')->auth_rebuild_required_at)->toBeNull();
});

test('deleteScope cascades into token.scopes and on-disk primitive scopes', function () {
    // Two scopes, a token referencing both, and a spec with one scoped primitive.
    $this->svc->createScope('demo', 'read');
    $this->svc->createScope('demo', 'write');
    $this->svc->mintToken('demo', 'CI', ['read', 'write']);

    $spec = new ServerSpec(
        name: 'demo',
        primitives: [[
            'kind' => 'tool', 'name' => 'hello',
            'description' => 'd', 'code' => 'return "ok"',
            'scopes' => ['read', 'write'],
        ]],
    );
    $this->store->save($spec);

    $this->svc->deleteScope('demo', 'read');

    // Scope row gone.
    expect(ServerScope::where('server_name', 'demo')->where('name', 'read')->exists())->toBeFalse();
    // Token's scope list scrubbed.
    expect(ServerToken::where('server_name', 'demo')->first()->scopes)->toBe(['write']);
    // Primitive's scope list scrubbed on disk.
    $reloaded = $this->store->load('demo');
    expect($reloaded->primitives[0]['scopes'])->toBe(['write']);
});

test('renameScope replaces the name everywhere', function () {
    $this->svc->createScope('demo', 'read');
    $this->svc->mintToken('demo', 'CI', ['read']);

    $spec = new ServerSpec(
        name: 'demo',
        primitives: [[
            'kind' => 'tool', 'name' => 'hello',
            'description' => 'd', 'code' => 'return "ok"',
            'scopes' => ['read'],
        ]],
    );
    $this->store->save($spec);

    $this->svc->renameScope('demo', 'read', 'view');

    expect(ServerScope::where('server_name', 'demo')->where('name', 'view')->exists())->toBeTrue();
    expect(ServerScope::where('server_name', 'demo')->where('name', 'read')->exists())->toBeFalse();
    expect(ServerToken::where('server_name', 'demo')->first()->scopes)->toBe(['view']);
    expect($this->store->load('demo')->primitives[0]['scopes'])->toBe(['view']);
});

test('createScope enforces unique name per server', function () {
    $this->svc->createScope('demo', 'read');
    expect(fn () => $this->svc->createScope('demo', 'read'))
        ->toThrow(\Illuminate\Database\QueryException::class);
});

test('mintToken enforces unique name per server', function () {
    $this->svc->mintToken('demo', 'CI', []);
    expect(fn () => $this->svc->mintToken('demo', 'CI', []))
        ->toThrow(\Illuminate\Database\QueryException::class);
});

test('tokensForCodegen returns decrypted plaintext with scopes', function () {
    $minted = $this->svc->mintToken('demo', 'CI', ['read']);
    $hydrated = $this->svc->tokensForCodegen('demo');
    expect($hydrated)->toHaveCount(1);
    expect($hydrated[0]['name'])->toBe('CI');
    expect($hydrated[0]['token'])->toBe($minted['plaintext']);
    expect($hydrated[0]['scopes'])->toBe(['read']);
});
