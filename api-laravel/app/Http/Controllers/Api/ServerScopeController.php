<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\ServerScope;
use App\Models\User;
use App\Services\Mcp\ServerAuthService;
use App\Services\Mcp\ServerPermissions;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Validation\Rule;
use Symfony\Component\HttpKernel\Exception\HttpException;

class ServerScopeController extends Controller
{
    public function __construct(
        private readonly ServerAuthService $auth,
        private readonly ServerPermissions $perms,
    ) {}

    public function index(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $scopes = ServerScope::where('server_name', $name)
            ->orderBy('name')
            ->get(['id', 'name', 'description', 'created_at', 'updated_at']);
        return response()->json($scopes);
    }

    public function store(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $data = $request->validate([
            'name' => [
                'required', 'string', 'max:64',
                'regex:/^[a-zA-Z0-9_:.-]+$/',
                Rule::unique('server_scopes', 'name')->where('server_name', $name),
            ],
            'description' => ['sometimes', 'nullable', 'string', 'max:255'],
        ]);

        $scope = $this->auth->createScope($name, $data['name'], $data['description'] ?? null);
        return response()->json($scope, 201);
    }

    public function update(Request $request, string $name, string $scopeName): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $scope = ServerScope::where('server_name', $name)->where('name', $scopeName)->first();
        if (! $scope) {
            throw new HttpException(404, "Scope '{$scopeName}' not found.");
        }
        $data = $request->validate([
            'name' => [
                'sometimes', 'string', 'max:64',
                'regex:/^[a-zA-Z0-9_:.-]+$/',
                Rule::unique('server_scopes', 'name')->where('server_name', $name)->ignore($scope->id),
            ],
            'description' => ['sometimes', 'nullable', 'string', 'max:255'],
        ]);

        if (isset($data['name']) && $data['name'] !== $scope->name) {
            $this->auth->renameScope($name, $scope->name, $data['name']);
            $scope = ServerScope::where('server_name', $name)->where('name', $data['name'])->first();
        }
        if (array_key_exists('description', $data)) {
            $scope->description = $data['description'];
            $scope->save();
            $this->auth->markRebuildRequired($name);
        }
        return response()->json($scope);
    }

    public function destroy(Request $request, string $name, string $scopeName): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $exists = ServerScope::where('server_name', $name)->where('name', $scopeName)->exists();
        if (! $exists) {
            throw new HttpException(404, "Scope '{$scopeName}' not found.");
        }
        $this->auth->deleteScope($name, $scopeName);
        return response()->json(null, 204);
    }

    private function assertAccess(User $user, string $serverName): void
    {
        if (! $this->perms->canAccess($user, $serverName)) {
            throw new HttpException(403, 'Access denied');
        }
    }
}
