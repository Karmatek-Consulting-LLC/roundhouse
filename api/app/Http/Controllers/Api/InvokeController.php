<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\Mcp\DockerClient;
use App\Services\Mcp\McpClient;
use App\Services\Mcp\McpException;
use App\Services\Mcp\ServerPermissions;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Symfony\Component\HttpKernel\Exception\HttpException;

/**
 * Thin pass-through from the frontend to the MCP server's JSON-RPC endpoint.
 * Gated on the same per-server permission model as the rest of the API.
 */
class InvokeController extends Controller
{
    public function __construct(
        private readonly McpClient $mcp,
        private readonly DockerClient $docker,
        private readonly ServerPermissions $perms,
    ) {}

    public function listTools(Request $request, string $name): JsonResponse
    {
        $this->assertAccessAndDeployed($request, $name);
        return response()->json(['tools' => $this->try(fn () => $this->mcp->listTools($name))]);
    }

    public function listResources(Request $request, string $name): JsonResponse
    {
        $this->assertAccessAndDeployed($request, $name);
        return response()->json(['resources' => $this->try(fn () => $this->mcp->listResources($name))]);
    }

    public function listPrompts(Request $request, string $name): JsonResponse
    {
        $this->assertAccessAndDeployed($request, $name);
        return response()->json(['prompts' => $this->try(fn () => $this->mcp->listPrompts($name))]);
    }

    public function invokeTool(Request $request, string $name): JsonResponse
    {
        $data = $request->validate([
            'tool' => ['required', 'string'],
            'arguments' => ['sometimes', 'array'],
        ]);
        $this->assertAccessAndDeployed($request, $name);

        return response()->json(
            $this->try(fn () => $this->mcp->callTool($name, $data['tool'], $data['arguments'] ?? []))
        );
    }

    public function readResource(Request $request, string $name): JsonResponse
    {
        $data = $request->validate([
            'uri' => ['required', 'string'],
        ]);
        $this->assertAccessAndDeployed($request, $name);

        return response()->json(
            $this->try(fn () => $this->mcp->readResource($name, $data['uri']))
        );
    }

    public function getPrompt(Request $request, string $name): JsonResponse
    {
        $data = $request->validate([
            'prompt' => ['required', 'string'],
            'arguments' => ['sometimes', 'array'],
        ]);
        $this->assertAccessAndDeployed($request, $name);

        return response()->json(
            $this->try(fn () => $this->mcp->getPrompt($name, $data['prompt'], $data['arguments'] ?? []))
        );
    }

    private function assertAccessAndDeployed(Request $request, string $name): void
    {
        if (! $this->perms->canAccess($request->user(), $name)) {
            throw new HttpException(403, 'Access denied');
        }
        if (! $this->docker->getServer($name)) {
            throw new HttpException(409, "Server '{$name}' is not deployed — nothing to invoke against.");
        }
    }

    private function try(\Closure $fn): mixed
    {
        try {
            return $fn();
        } catch (McpException $e) {
            // 502 = upstream MCP server failed or returned an error
            throw new HttpException(502, $e->getMessage());
        }
    }
}
