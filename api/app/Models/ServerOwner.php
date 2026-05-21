<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

#[Fillable(['server_name', 'owner_id', 'auth_rebuild_required_at'])]
class ServerOwner extends Model
{
    protected $table = 'server_owners';

    protected $primaryKey = 'server_name';

    protected $keyType = 'string';

    public $incrementing = false;

    public $timestamps = false;

    protected function casts(): array
    {
        return [
            'created_at' => 'datetime',
            'auth_rebuild_required_at' => 'datetime',
        ];
    }

    public function owner(): BelongsTo
    {
        return $this->belongsTo(User::class, 'owner_id');
    }
}
