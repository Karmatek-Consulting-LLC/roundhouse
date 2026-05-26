<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Model;

#[Fillable(['server_name', 'name', 'description'])]
class ServerScope extends Model
{
    protected $table = 'server_scopes';
}
