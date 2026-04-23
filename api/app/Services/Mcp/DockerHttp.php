<?php

namespace App\Services\Mcp;

use GuzzleHttp\Client;
use GuzzleHttp\Exception\ClientException;
use GuzzleHttp\Exception\RequestException;
use Psr\Http\Message\ResponseInterface;

/**
 * Low-level Docker Engine HTTP API client over a unix socket.
 * Mirrors the functions of the Python ``docker`` SDK we actually need.
 */
class DockerHttp
{
    private const API_VERSION = 'v1.43';

    private Client $http;

    public function __construct(string $socketPath)
    {
        $this->http = new Client([
            'base_uri' => 'http://docker/'.self::API_VERSION.'/',
            'http_errors' => false,
            'curl' => [CURLOPT_UNIX_SOCKET_PATH => $socketPath],
            'timeout' => 120,
            'connect_timeout' => 10,
        ]);
    }

    /** @param array<string, mixed> $options */
    public function request(string $method, string $path, array $options = []): ResponseInterface
    {
        try {
            return $this->http->request($method, ltrim($path, '/'), $options);
        } catch (ClientException|RequestException $e) {
            $resp = $e->getResponse();
            if ($resp) {
                return $resp;
            }
            throw new DockerException("Docker API request failed: {$e->getMessage()}", previous: $e);
        }
    }

    /** @param array<string, mixed> $query */
    public function get(string $path, array $query = []): array
    {
        $resp = $this->request('GET', $this->buildPath($path, $query));
        return $this->expectJson($resp, $path);
    }

    /**
     * @param array<string, mixed> $query
     * @param array<string, mixed>|null $body  JSON body when provided
     */
    public function post(string $path, array $query = [], ?array $body = null, array $headers = []): array
    {
        $opts = [];
        if ($body !== null) {
            $opts['json'] = $body;
        }
        if ($headers) {
            $opts['headers'] = $headers;
        }
        $resp = $this->request('POST', $this->buildPath($path, $query), $opts);
        $code = $resp->getStatusCode();
        $rawBody = (string) $resp->getBody();
        if ($code >= 400) {
            throw $this->toException($code, $rawBody, $path);
        }
        if ($rawBody === '') {
            return [];
        }
        $decoded = json_decode($rawBody, true);
        return is_array($decoded) ? $decoded : [];
    }

    /** @param array<string, mixed> $query */
    public function delete(string $path, array $query = []): void
    {
        $resp = $this->request('DELETE', $this->buildPath($path, $query));
        $code = $resp->getStatusCode();
        if ($code >= 400 && $code !== 404) {
            throw $this->toException($code, (string) $resp->getBody(), $path);
        }
    }

    /**
     * Streamed POST — returns an iterable of parsed JSON line frames.
     * Used by image build and image push endpoints.
     *
     * @param resource|string $body
     * @param array<string, mixed> $query
     * @param array<string, string> $headers
     * @return iterable<int, array<string, mixed>>
     */
    public function postStream(string $path, array $query, $body, array $headers = []): iterable
    {
        // Note: we can't use Guzzle's stream=>true over a unix socket — that forces the
        // PHP StreamHandler which doesn't support CURLOPT_UNIX_SOCKET_PATH. We buffer
        // the whole response (Docker build/push progress is modest) and parse NDJSON.
        $opts = [
            'body' => $body,
            'headers' => $headers,
            'timeout' => 600,
        ];
        $resp = $this->request('POST', $this->buildPath($path, $query), $opts);
        $code = $resp->getStatusCode();
        $raw = (string) $resp->getBody();

        if ($code >= 400) {
            throw $this->toException($code, $raw, $path);
        }

        foreach (preg_split('/\r?\n/', $raw) as $line) {
            $line = trim($line);
            if ($line === '') {
                continue;
            }
            $json = json_decode($line, true);
            if (is_array($json)) {
                yield $json;
            }
        }
    }

    /**
     * Raw streaming GET — returns the full response body (used for logs).
     *
     * @param array<string, mixed> $query
     */
    public function getRaw(string $path, array $query = []): string
    {
        $resp = $this->request('GET', $this->buildPath($path, $query));
        $code = $resp->getStatusCode();
        if ($code >= 400) {
            throw $this->toException($code, (string) $resp->getBody(), $path);
        }
        return (string) $resp->getBody();
    }

    /** Demultiplex Docker stdout/stderr log frames (no TTY case). */
    public static function demuxLogFrames(string $raw): string
    {
        $out = '';
        $len = strlen($raw);
        $pos = 0;
        while ($pos + 8 <= $len) {
            // Header: [stream(1), 0, 0, 0, size(4 BE)]
            $stream = ord($raw[$pos]);
            $size = unpack('N', substr($raw, $pos + 4, 4))[1] ?? 0;
            if ($stream > 2 || $size < 0) {
                // Unrecognized header — assume raw text
                return $raw;
            }
            $pos += 8;
            if ($pos + $size > $len) {
                $out .= substr($raw, $pos);
                break;
            }
            $out .= substr($raw, $pos, $size);
            $pos += $size;
        }
        return $out;
    }

    /** @param array<string, mixed> $query */
    private function buildPath(string $path, array $query): string
    {
        $path = ltrim($path, '/');
        if (! $query) {
            return $path;
        }
        $encoded = [];
        foreach ($query as $k => $v) {
            if ($v === null) {
                continue;
            }
            if (is_bool($v)) {
                $encoded[$k] = $v ? '1' : '0';
            } elseif (is_array($v) || is_object($v)) {
                $encoded[$k] = json_encode($v);
            } else {
                $encoded[$k] = (string) $v;
            }
        }
        return $path.'?'.http_build_query($encoded);
    }

    /** @return array<string, mixed> */
    private function expectJson(ResponseInterface $resp, string $path): array
    {
        $code = $resp->getStatusCode();
        $body = (string) $resp->getBody();
        if ($code === 404) {
            throw new DockerNotFoundException("Not found: {$path}");
        }
        if ($code >= 400) {
            throw $this->toException($code, $body, $path);
        }
        $decoded = json_decode($body, true);
        return is_array($decoded) ? $decoded : [];
    }

    private function toException(int $code, string $body, string $path): DockerException
    {
        $message = $body;
        $decoded = json_decode($body, true);
        if (is_array($decoded) && isset($decoded['message'])) {
            $message = (string) $decoded['message'];
        }
        if ($code === 404) {
            return new DockerNotFoundException("Not found: {$path} — {$message}", $code);
        }
        return new DockerException("Docker API {$code} on {$path}: {$message}", $code);
    }
}
