<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Model;

#[Fillable(['server_name', 'name', 'token', 'display_prefix', 'scopes'])]
class ServerToken extends Model
{
    protected $table = 'server_tokens';

    protected function casts(): array
    {
        return [
            'token' => 'encrypted',
            'scopes' => 'array',
        ];
    }
}
