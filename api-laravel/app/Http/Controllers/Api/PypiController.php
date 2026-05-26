<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Http;

class PypiController extends Controller
{
    private const CACHE_KEY = 'pypi:package-index';
    private const CACHE_TTL = 3600;

    public function search(Request $request): JsonResponse
    {
        $data = $request->validate([
            'q' => ['required', 'string', 'min:2'],
        ]);

        $query = strtolower(trim($data['q']));
        $normalizedQuery = preg_replace('/[-_.]/', '-', $query);

        $packages = $this->loadPackageIndex();

        $matches = [];
        foreach ($packages as $name) {
            $normalizedName = preg_replace('/[-_.]/', '-', strtolower($name));
            if (str_starts_with($normalizedName, $normalizedQuery)) {
                $matches[] = $name;
                if (count($matches) >= 10) {
                    break;
                }
            }
        }

        $results = [];
        foreach ($matches as $name) {
            try {
                $resp = Http::timeout(5)->get("https://pypi.org/pypi/{$name}/json");
                if ($resp->ok()) {
                    $info = $resp->json('info') ?? [];
                    $results[] = [
                        'name' => $info['name'] ?? $name,
                        'version' => $info['version'] ?? '',
                        'summary' => $info['summary'] ?? '',
                    ];
                    continue;
                }
            } catch (\Throwable) {
                // fall through
            }
            $results[] = ['name' => $name, 'version' => '', 'summary' => ''];
        }

        return response()->json($results);
    }

    /** @return string[] */
    private function loadPackageIndex(): array
    {
        return Cache::remember(self::CACHE_KEY, self::CACHE_TTL, function () {
            // PyPI's full index is ~50MB of JSON with 600k+ projects - easily blows the
            // default 128M PHP memory limit during decode. Bump for the rest of this request;
            // PHP's per-request model means it resets for the next one.
            ini_set('memory_limit', '512M');

            $resp = Http::timeout(30)
                ->withHeaders(['Accept' => 'application/vnd.pypi.simple.v1+json'])
                ->get('https://pypi.org/simple/');
            $resp->throw();

            $body = (string) $resp->body();
            $data = json_decode($body, true, flags: JSON_THROW_ON_ERROR);
            unset($body);

            $names = [];
            foreach ($data['projects'] ?? [] as $p) {
                if (isset($p['name'])) {
                    $names[] = $p['name'];
                }
            }
            return $names;
        });
    }
}
