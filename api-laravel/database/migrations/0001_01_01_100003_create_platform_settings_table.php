<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('platform_settings', function (Blueprint $table) {
            $table->string('key', 255)->primary();
            $table->text('value')->default('');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('platform_settings');
    }
};
