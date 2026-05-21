<?php

namespace App\Services\Mcp;

use App\Models\ServerOwner;
use App\Models\ServerScope;
use App\Models\ServerToken;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;

/**
 * Manages per-server scopes and bearer tokens for the runtime auth surface
 * exposed by generated FastMCP servers (StaticTokenVerifier + require_scopes).
 *
 * Referential integrity is enforced here, not by DB constraints: scope rows
 * own a name, and that name appears as a string inside ServerToken.scopes and
 * inside primitive specs on disk. Delete/rename cascades fan out from this
 * service - nowhere else should mutate those references.
 */
class ServerAuthService
{
    public function __construct(public readonly ServerStore $store) {}

    public function createScope(string $server, string $name, ?string $description = null): ServerScope
    {
        $scope = ServerScope::create([
            'server_name' => $server,
            'name' => $name,
            'description' => $description,
        ]);
        $this->markRebuildRequired($server);
        return $scope;
    }

    /**
     * Drop a scope row and scrub its name from every reference within the server.
     * On-disk spec is mutated first so a DB failure leaves no orphan primitive refs.
     */
    public function deleteScope(string $server, string $name): void
    {
        $this->mutatePrimitiveScopes($server, fn (array $scopes) => array_values(array_filter(
            $scopes,
            fn ($s) => $s !== $name,
        )));

        DB::transaction(function () use ($server, $name) {
            ServerToken::where('server_name', $server)->get()->each(function (ServerToken $tok) use ($name) {
                $current = (array) ($tok->scopes ?? []);
                $next = array_values(array_filter($current, fn ($s) => $s !== $name));
                if ($next !== $current) {
                    $tok->scopes = $next;
                    $tok->save();
                }
            });
            ServerScope::where('server_name', $server)->where('name', $name)->delete();
        });

        $this->markRebuildRequired($server);
    }

    public function renameScope(string $server, string $oldName, string $newName): void
    {
        $this->mutatePrimitiveScopes($server, fn (array $scopes) => array_values(array_map(
            fn ($s) => $s === $oldName ? $newName : $s,
            $scopes,
        )));

        DB::transaction(function () use ($server, $oldName, $newName) {
            ServerToken::where('server_name', $server)->get()->each(function (ServerToken $tok) use ($oldName, $newName) {
                $current = (array) ($tok->scopes ?? []);
                $next = array_values(array_map(fn ($s) => $s === $oldName ? $newName : $s, $current));
                if ($next !== $current) {
                    $tok->scopes = $next;
                    $tok->save();
                }
            });
            ServerScope::where('server_name', $server)->where('name', $oldName)
                ->update(['name' => $newName]);
        });

        $this->markRebuildRequired($server);
    }

    /**
     * Generate a token, persist it encrypted, and return the plaintext exactly once.
     *
     * @param  string[]  $scopes
     * @return array{id:int, plaintext:string, token:ServerToken}
     */
    public function mintToken(string $server, string $name, array $scopes = []): array
    {
        $plain = $this->generateTokenString();
        $token = ServerToken::create([
            'server_name' => $server,
            'name' => $name,
            'token' => $plain,
            'display_prefix' => substr($plain, 0, 12),
            'scopes' => array_values(array_unique(array_filter(
                $scopes,
                fn ($s) => is_string($s) && $s !== '',
            ))),
        ]);
        $this->markRebuildRequired($server);
        return ['id' => $token->id, 'plaintext' => $plain, 'token' => $token];
    }

    public function revokeToken(string $server, int $id): bool
    {
        $deleted = ServerToken::where('server_name', $server)->where('id', $id)->delete();
        if ($deleted) {
            $this->markRebuildRequired($server);
        }
        return (bool) $deleted;
    }

    /**
     * Hydrate plaintext tokens for codegen. Only call from the build pipeline -
     * the plaintext should not leave the generated server.py.
     *
     * @return array<int, array{name:string, token:string, scopes:string[]}>
     */
    public function tokensForCodegen(string $server): array
    {
        return ServerToken::where('server_name', $server)
            ->orderBy('id')
            ->get()
            ->map(fn (ServerToken $t) => [
                'name' => $t->name,
                'token' => $t->token, // decrypted by the encrypted cast
                'scopes' => (array) ($t->scopes ?? []),
            ])
            ->all();
    }

    public function markRebuildRequired(string $server): void
    {
        ServerOwner::where('server_name', $server)
            ->update(['auth_rebuild_required_at' => Carbon::now()]);
    }

    public function clearRebuildRequired(string $server): void
    {
        ServerOwner::where('server_name', $server)
            ->update(['auth_rebuild_required_at' => null]);
    }

    private function generateTokenString(): string
    {
        return 'mcps_'.rtrim(strtr(base64_encode(random_bytes(36)), '+/', '-_'), '=');
    }

    /**
     * Walk every primitive in the server's spec and apply $mutator to its scopes list.
     * Persists the spec only if anything changed.
     */
    private function mutatePrimitiveScopes(string $server, callable $mutator): void
    {
        $spec = $this->store->load($server);
        if ($spec === null) {
            return;
        }
        $changed = false;
        foreach ($spec->primitives as $i => $p) {
            $current = (array) ($p['scopes'] ?? []);
            if (! $current) {
                continue;
            }
            $next = array_values($mutator($current));
            if ($next !== $current) {
                $spec->primitives[$i]['scopes'] = $next;
                $changed = true;
            }
        }
        if ($changed) {
            $this->store->save($spec);
        }
    }
}
