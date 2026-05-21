<?php

use App\Http\Controllers\Api\AuthController;
use App\Http\Controllers\Api\InvokeController;
use App\Http\Controllers\Api\PypiController;
use App\Http\Controllers\Api\ServerController;
use App\Http\Controllers\Api\ServerScopeController;
use App\Http\Controllers\Api\ServerTokenController;
use App\Http\Controllers\Api\SettingsController;
use App\Http\Controllers\Api\TeamController;
use App\Http\Controllers\Api\TemplateController;
use App\Http\Controllers\Api\UserController;
use Illuminate\Support\Facades\Route;

Route::get('/health', fn () => ['status' => 'ok']);

Route::prefix('auth')->group(function () {
    Route::post('/login', [AuthController::class, 'login']);

    Route::middleware('auth:sanctum')->group(function () {
        Route::get('/me', [AuthController::class, 'me']);
        Route::post('/change-password', [AuthController::class, 'changePassword']);
        Route::post('/register', [AuthController::class, 'register'])->middleware('superadmin');
    });
});

Route::middleware('auth:sanctum')->group(function () {
    // Users - superadmin only
    Route::middleware('superadmin')->group(function () {
        Route::get('/users', [UserController::class, 'index']);
        Route::get('/users/{user_id}', [UserController::class, 'show']);
        Route::put('/users/{user_id}/password', [UserController::class, 'setPassword']);
        Route::delete('/users/{user_id}', [UserController::class, 'destroy']);
    });

    // Teams - mixed permissions (checked in controller where needed)
    Route::get('/teams', [TeamController::class, 'index']);
    Route::get('/teams/{team_id}', [TeamController::class, 'show']);
    Route::put('/teams/{team_id}', [TeamController::class, 'update']);
    Route::post('/teams/{team_id}/members', [TeamController::class, 'addMember']);
    Route::put('/teams/{team_id}/members/{user_id}', [TeamController::class, 'updateMember']);
    Route::delete('/teams/{team_id}/members/{user_id}', [TeamController::class, 'removeMember']);

    // Team create/delete - superadmin only
    Route::middleware('superadmin')->group(function () {
        Route::post('/teams', [TeamController::class, 'store']);
        Route::delete('/teams/{team_id}', [TeamController::class, 'destroy']);
    });

    // Templates - any authed user
    Route::get('/templates', [TemplateController::class, 'index']);
    Route::get('/templates/{name}', [TemplateController::class, 'show']);

    // Servers
    Route::get('/servers', [ServerController::class, 'index']);
    Route::get('/servers/limits', [ServerController::class, 'limits']);
    Route::get('/servers/{name}', [ServerController::class, 'show']);
    Route::get('/servers/{name}/logs', [ServerController::class, 'logs']);
    Route::post('/servers', [ServerController::class, 'store']);
    Route::post('/servers/{name}/start', [ServerController::class, 'start']);
    Route::post('/servers/{name}/stop', [ServerController::class, 'stop']);
    Route::post('/servers/{name}/redeploy', [ServerController::class, 'redeploy']);
    Route::delete('/servers/{name}', [ServerController::class, 'destroy']);
    Route::put('/servers/{name}/description', [ServerController::class, 'updateDescription']);
    Route::put('/servers/{name}/replicas', [ServerController::class, 'updateReplicas']);
    Route::post('/servers/{name}/primitives', [ServerController::class, 'addPrimitive']);
    Route::put('/servers/{name}/primitives/{prim_name}', [ServerController::class, 'updatePrimitive']);
    Route::delete('/servers/{name}/primitives/{prim_name}', [ServerController::class, 'deletePrimitive']);
    Route::put('/servers/{name}/packages', [ServerController::class, 'updatePackages']);
    Route::put('/servers/{name}/apt-packages', [ServerController::class, 'updateAptPackages']);
    Route::put('/servers/{name}/env', [ServerController::class, 'updateEnv']);
    Route::put('/servers/{name}/config', [ServerController::class, 'updateConfig']);
    Route::put('/servers/{name}/source', [ServerController::class, 'updateSource']);

    // Server runtime auth (FastMCP StaticTokenVerifier + require_scopes).
    Route::get('/servers/{name}/scopes', [ServerScopeController::class, 'index']);
    Route::post('/servers/{name}/scopes', [ServerScopeController::class, 'store']);
    Route::put('/servers/{name}/scopes/{scope_name}', [ServerScopeController::class, 'update']);
    Route::delete('/servers/{name}/scopes/{scope_name}', [ServerScopeController::class, 'destroy']);
    Route::get('/servers/{name}/tokens', [ServerTokenController::class, 'index']);
    Route::post('/servers/{name}/tokens', [ServerTokenController::class, 'store']);
    Route::delete('/servers/{name}/tokens/{id}', [ServerTokenController::class, 'destroy'])->whereNumber('id');

    // Invoke live MCP primitives (JSON-RPC pass-through, gated by ServerPermissions).
    Route::get('/servers/{name}/tools', [InvokeController::class, 'listTools']);
    Route::get('/servers/{name}/resources', [InvokeController::class, 'listResources']);
    Route::get('/servers/{name}/prompts', [InvokeController::class, 'listPrompts']);
    Route::post('/servers/{name}/tools/invoke', [InvokeController::class, 'invokeTool']);
    Route::post('/servers/{name}/resources/read', [InvokeController::class, 'readResource']);
    Route::post('/servers/{name}/prompts/get', [InvokeController::class, 'getPrompt']);

    // PyPI - any authed user
    Route::get('/pypi/search', [PypiController::class, 'search']);

    // Settings - superadmin only
    Route::middleware('superadmin')->group(function () {
        Route::get('/settings', [SettingsController::class, 'index']);
        Route::put('/settings/hostname', [SettingsController::class, 'updateHostname']);
        Route::put('/settings/docker-registry', [SettingsController::class, 'updateDockerRegistry']);
        Route::post('/settings/certificate', [SettingsController::class, 'uploadCertificate']);
        Route::delete('/settings/certificate', [SettingsController::class, 'deleteCertificate']);
        Route::put('/settings/custom-ca', [SettingsController::class, 'updateCustomCa']);
        Route::delete('/settings/custom-ca', [SettingsController::class, 'deleteCustomCa']);
        Route::get('/settings/mcp-env', [SettingsController::class, 'getMcpEnv']);
        Route::put('/settings/mcp-env', [SettingsController::class, 'putMcpEnv']);
    });
});
