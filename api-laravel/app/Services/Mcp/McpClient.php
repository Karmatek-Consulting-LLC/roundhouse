<?php

namespace App\Services\Mcp;

use GuzzleHttp\Client;
use GuzzleHttp\Exception\ConnectException;
use GuzzleHttp\Exception\GuzzleException;

/**
 * Thin JSON-RPC client for MCP servers we spawn.
 *
 * Our generated FastMCP servers run with stateless_http=True + json_response=True,
 * which means every request stands alone - no initialize handshake, no SSE streaming,
 * just POST JSON → get JSON.
 *
 * Server URL resolution prefers Docker DNS (mcp-{name}:8000/mcp) so we don't depend
 * on Traefik being up, and we avoid the public round-trip for an internal call.
 */
class McpClient
{
    private Client $http;

    public function __construct()
    {
        $this->http = new Client([
            'http_errors' => false,
            'connect_timeout' => 3,
            'timeout' => 30,
            'headers' => [
                // FastMCP's streamable-http transport rejects requests that don't
                // advertise both content types in Accept.
                'Accept' => 'application/json, text/event-stream',
                'Content-Type' => 'application/json',
            ],
        ]);
    }

    /**
     * Invoke a JSON-RPC method on the named MCP server.
     *
     * @param  array<string, mixed>  $params
     * @return array<string, mixed>  The unwrapped `result` object
     * @throws McpException  on transport failure or JSON-RPC error
     */
    public function call(string $serverName, string $method, array $params = []): array
    {
        $url = $this->serverUrl($serverName);
        $envelope = [
            'jsonrpc' => '2.0',
            'id' => random_int(1, PHP_INT_MAX),
            'method' => $method,
            // Force JSON object - PHP's empty [] serializes as [], but MCP requires {}.
            'params' => empty($params) ? new \stdClass() : $params,
        ];

        try {
            $resp = $this->http->post($url, ['json' => $envelope]);
        } catch (ConnectException $e) {
            throw new McpException("Cannot reach MCP server '{$serverName}' ({$e->getMessage()})");
        } catch (GuzzleException $e) {
            throw new McpException("MCP request failed: {$e->getMessage()}");
        }

        $code = $resp->getStatusCode();
        $body = (string) $resp->getBody();

        if ($code >= 400) {
            throw new McpException("MCP server returned HTTP {$code}: ".substr($body, 0, 300));
        }

        $decoded = json_decode($body, true);
        if (! is_array($decoded)) {
            throw new McpException('MCP server returned non-JSON response: '.substr($body, 0, 200));
        }

        if (isset($decoded['error'])) {
            $message = $decoded['error']['message'] ?? 'Unknown JSON-RPC error';
            $errCode = $decoded['error']['code'] ?? -1;
            throw new McpException("MCP error {$errCode}: {$message}");
        }

        $result = $decoded['result'] ?? null;
        if (! is_array($result)) {
            throw new McpException('MCP response missing result object');
        }
        return $result;
    }

    // --- Typed helpers over call() ---

    /** @return array<int, array<string, mixed>> */
    public function listTools(string $serverName): array
    {
        $result = $this->call($serverName, 'tools/list');
        return $result['tools'] ?? [];
    }

    /** @return array<int, array<string, mixed>> */
    public function listResources(string $serverName): array
    {
        $result = $this->call($serverName, 'resources/list');
        $out = $result['resources'] ?? [];
        foreach ($result['resourceTemplates'] ?? [] as $t) {
            $out[] = $t + ['isTemplate' => true];
        }
        return $out;
    }

    /** @return array<int, array<string, mixed>> */
    public function listPrompts(string $serverName): array
    {
        $result = $this->call($serverName, 'prompts/list');
        return $result['prompts'] ?? [];
    }

    /** @param array<string, mixed> $arguments  @return array<string, mixed> */
    public function callTool(string $serverName, string $toolName, array $arguments): array
    {
        return $this->call($serverName, 'tools/call', [
            'name' => $toolName,
            'arguments' => (object) $arguments, // force JSON object, not empty array
        ]);
    }

    /** @return array<string, mixed> */
    public function readResource(string $serverName, string $uri): array
    {
        return $this->call($serverName, 'resources/read', ['uri' => $uri]);
    }

    /** @param array<string, mixed> $arguments  @return array<string, mixed> */
    public function getPrompt(string $serverName, string $promptName, array $arguments): array
    {
        return $this->call($serverName, 'prompts/get', [
            'name' => $promptName,
            'arguments' => (object) $arguments,
        ]);
    }

    private function serverUrl(string $serverName): string
    {
        // Inside the docker network: mcp-{name}:8000 resolves via Docker DNS both in
        // standalone containers and in Swarm (VIP endpoint). No dependency on Traefik.
        $host = DockerClient::CONTAINER_PREFIX.$serverName;
        return "http://{$host}:8000/mcp";
    }
}
