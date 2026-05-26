<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('server_owners', function (Blueprint $table) {
            $table->string('server_name', 255)->primary();
            $table->uuid('owner_id')->index();
            $table->timestamp('created_at')->useCurrent();

            $table->foreign('owner_id')->references('id')->on('users')->cascadeOnDelete();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('server_owners');
    }
};
