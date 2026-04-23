<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Hidden;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Relations\BelongsToMany;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Foundation\Auth\User as Authenticatable;
use Laravel\Sanctum\HasApiTokens;

#[Fillable(['email', 'password_hash', 'display_name', 'role'])]
#[Hidden(['password_hash'])]
class User extends Authenticatable
{
    use HasApiTokens, HasUuids;

    public $timestamps = false;

    protected function casts(): array
    {
        return [
            'created_at' => 'datetime',
        ];
    }

    public function getAuthPassword(): string
    {
        return $this->password_hash;
    }

    public function isSuperadmin(): bool
    {
        return $this->role === 'superadmin';
    }

    public function memberships(): HasMany
    {
        return $this->hasMany(TeamMembership::class, 'user_id');
    }

    public function teams(): BelongsToMany
    {
        return $this->belongsToMany(Team::class, 'team_memberships', 'user_id', 'team_id')
            ->withPivot('role');
    }

    public function ownedServers(): HasMany
    {
        return $this->hasMany(ServerOwner::class, 'owner_id');
    }

    public function toApiArray(): array
    {
        return [
            'id' => (string) $this->id,
            'email' => $this->email,
            'display_name' => $this->display_name,
            'role' => $this->role,
        ];
    }
}
