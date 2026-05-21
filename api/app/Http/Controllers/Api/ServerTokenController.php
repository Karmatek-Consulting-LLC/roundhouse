<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\ServerScope;
use App\Models\ServerToken;
use App\Models\User;
use App\Services\Mcp\ServerAuthService;
use App\Services\Mcp\ServerPermissions;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Validation\Rule;
use Symfony\Component\HttpKernel\Exception\HttpException;

class ServerTokenController extends Controller
{
    public function __construct(
        private readonly ServerAuthService $auth,
        private readonly ServerPermissions $perms,
    ) {}

    public function index(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        // Plaintext token column is loaded by the cast but explicitly excluded
        // from the response shape - the UI only ever sees the display_prefix.
        $tokens = ServerToken::where('server_name', $name)
            ->orderBy('id')
            ->get()
            ->map(fn (ServerToken $t) => [
                'id' => $t->id,
                'name' => $t->name,
                'display_prefix' => $t->display_prefix,
                'scopes' => (array) ($t->scopes ?? []),
                'created_at' => $t->created_at,
            ]);
        return response()->json($tokens);
    }

    public function store(Request $request, string $name): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        $data = $request->validate([
            'name' => [
                'required', 'string', 'max:64',
                Rule::unique('server_tokens', 'name')->where('server_name', $name),
            ],
            'scopes' => ['sometimes', 'array'],
            'scopes.*' => ['string', 'max:64'],
        ]);

        $scopes = $data['scopes'] ?? [];
        $this->assertScopesExist($name, $scopes);

        $minted = $this->auth->mintToken($name, $data['name'], $scopes);
        $t = $minted['token'];
        return response()->json([
            'id' => $t->id,
            'name' => $t->name,
            'display_prefix' => $t->display_prefix,
            'scopes' => (array) ($t->scopes ?? []),
            'created_at' => $t->created_at,
            // Plaintext token returned exactly once. The client must store this
            // immediately - there is no way to retrieve it again.
            'token' => $minted['plaintext'],
        ], 201);
    }

    public function destroy(Request $request, string $name, int $id): JsonResponse
    {
        $this->assertAccess($request->user(), $name);
        if (! $this->auth->revokeToken($name, $id)) {
            throw new HttpException(404, "Token {$id} not found.");
        }
        return response()->json(null, 204);
    }

    /** @param string[] $scopes */
    private function assertScopesExist(string $server, array $scopes): void
    {
        if (! $scopes) {
            return;
        }
        $known = ServerScope::where('server_name', $server)
            ->whereIn('name', $scopes)
            ->pluck('name')
            ->all();
        $unknown = array_values(array_diff($scopes, $known));
        if ($unknown) {
            throw new HttpException(422, 'Unknown scopes: '.implode(', ', $unknown));
        }
    }

    private function assertAccess(User $user, string $serverName): void
    {
        if (! $this->perms->canAccess($user, $serverName)) {
            throw new HttpException(403, 'Access denied');
        }
    }
}
