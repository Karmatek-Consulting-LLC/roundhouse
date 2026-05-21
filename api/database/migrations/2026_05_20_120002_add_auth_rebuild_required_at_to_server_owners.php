<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('server_owners', function (Blueprint $table) {
            // Set when scopes/tokens/primitive-scope assignments change since the last
            // successful build. Cleared by ServerService on successful rebuild.
            $table->timestamp('auth_rebuild_required_at')->nullable()->after('created_at');
        });
    }

    public function down(): void
    {
        Schema::table('server_owners', function (Blueprint $table) {
            $table->dropColumn('auth_rebuild_required_at');
        });
    }
};
