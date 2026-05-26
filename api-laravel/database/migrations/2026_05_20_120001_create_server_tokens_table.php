<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('server_tokens', function (Blueprint $table) {
            $table->id();
            $table->string('server_name', 255)->index();
            $table->string('name', 64);
            $table->text('token');
            $table->string('display_prefix', 16);
            $table->json('scopes')->nullable();
            $table->timestamps();
            $table->unique(['server_name', 'name']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('server_tokens');
    }
};
