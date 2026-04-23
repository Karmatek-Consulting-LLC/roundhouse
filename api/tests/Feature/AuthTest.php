<?php

use App\Models\User;
use Illuminate\Support\Facades\Hash;

beforeEach(function () {
    $this->admin = User::query()->create([
        'email' => 'admin@test.local',
        'password_hash' => Hash::make('secret-pass'),
        'display_name' => 'Admin',
        'role' => 'superadmin',
    ]);
    $this->user = User::query()->create([
        'email' => 'alice@test.local',
        'password_hash' => Hash::make('alice-pass'),
        'display_name' => 'Alice',
        'role' => 'user',
    ]);
});

test('login returns access_token + user payload matching python contract', function () {
    $resp = $this->postJson('/api/auth/login', [
        'email' => 'admin@test.local',
        'password' => 'secret-pass',
    ]);
    $resp->assertOk();
    $resp->assertJsonStructure(['access_token', 'token_type', 'user' => ['id', 'email', 'display_name', 'role']]);
    expect($resp->json('token_type'))->toBe('bearer');
    expect($resp->json('user.role'))->toBe('superadmin');
});

test('invalid login returns 401 with detail key', function () {
    $resp = $this->postJson('/api/auth/login', [
        'email' => 'admin@test.local',
        'password' => 'wrong',
    ]);
    $resp->assertStatus(401);
    expect($resp->json('detail'))->toBe('Invalid email or password');
});

test('unauthenticated /me returns 401 JSON not redirect', function () {
    $resp = $this->getJson('/api/auth/me');
    $resp->assertStatus(401);
    expect($resp->json('detail'))->toBe('Not authenticated');
});

test('/me returns current user after login', function () {
    $token = $this->admin->createToken('test')->plainTextToken;
    $resp = $this->withHeader('Authorization', "Bearer {$token}")->getJson('/api/auth/me');
    $resp->assertOk();
    expect($resp->json('email'))->toBe('admin@test.local');
});

test('register requires superadmin and creates a new user', function () {
    $adminToken = $this->admin->createToken('t')->plainTextToken;
    $resp = $this->withHeader('Authorization', "Bearer {$adminToken}")
        ->postJson('/api/auth/register', [
            'email' => 'bob@test.local',
            'password' => 'password123',
            'display_name' => 'Bob',
        ]);
    $resp->assertStatus(201);
    expect(User::query()->where('email', 'bob@test.local')->exists())->toBeTrue();
});

test('register as non-superadmin returns 403', function () {
    $userToken = $this->user->createToken('t')->plainTextToken;
    $resp = $this->withHeader('Authorization', "Bearer {$userToken}")
        ->postJson('/api/auth/register', [
            'email' => 'carol@test.local',
            'password' => 'password123',
            'display_name' => 'Carol',
        ]);
    $resp->assertStatus(403);
});

test('change-password rejects wrong current password', function () {
    $token = $this->admin->createToken('t')->plainTextToken;
    $resp = $this->withHeader('Authorization', "Bearer {$token}")
        ->postJson('/api/auth/change-password', [
            'current_password' => 'nope',
            'new_password' => 'new-secret-pass',
        ]);
    $resp->assertStatus(400);
    expect($resp->json('detail'))->toBe('Current password is incorrect');
});
