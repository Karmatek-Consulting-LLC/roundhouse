<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('server_owners', function (Blueprint $table) {
            // Broaden the semantics: this column now signals "any spec change
            // needs a redeploy," not just auth changes.
            $table->renameColumn('auth_rebuild_required_at', 'redeploy_required_at');
        });
    }

    public function down(): void
    {
        Schema::table('server_owners', function (Blueprint $table) {
            $table->renameColumn('redeploy_required_at', 'auth_rebuild_required_at');
        });
    }
};
