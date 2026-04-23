<?php

namespace App\Services\Mcp;

use App\Models\ServerOwner;
use App\Models\TeamMembership;
use App\Models\User;

/**
 * Port of platform/app/permissions.py.
 */
class ServerPermissions
{
    public function canAccess(User $user, string $serverName): bool
    {
        if ($user->isSuperadmin()) {
            return true;
        }

        $owner = ServerOwner::query()->where('server_name', $serverName)->first();
        if (! $owner) {
            return false;
        }
        if ((string) $owner->owner_id === (string) $user->id) {
            return true;
        }

        $userTeams = TeamMembership::query()
            ->where('user_id', $user->id)
            ->pluck('team_id')
            ->all();
        if (! $userTeams) {
            return false;
        }

        return TeamMembership::query()
            ->where('user_id', $owner->owner_id)
            ->whereIn('team_id', $userTeams)
            ->exists();
    }

    /**
     * All server names the user may see. Null = superadmin (no filter).
     *
     * @return string[]|null
     */
    public function accessibleNames(User $user): ?array
    {
        if ($user->isSuperadmin()) {
            return null;
        }

        $own = ServerOwner::query()
            ->where('owner_id', $user->id)
            ->pluck('server_name')
            ->all();

        $userTeamIds = TeamMembership::query()
            ->where('user_id', $user->id)
            ->pluck('team_id');

        if ($userTeamIds->isEmpty()) {
            return array_values(array_unique($own));
        }

        $teammateIds = TeamMembership::query()
            ->whereIn('team_id', $userTeamIds)
            ->pluck('user_id');

        $teammateServers = ServerOwner::query()
            ->whereIn('owner_id', $teammateIds)
            ->pluck('server_name')
            ->all();

        return array_values(array_unique([...$own, ...$teammateServers]));
    }
}
