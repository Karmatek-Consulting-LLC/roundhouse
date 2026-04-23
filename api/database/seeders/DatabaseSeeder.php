<?php

namespace Database\Seeders;

use App\Models\ServerOwner;
use App\Models\User;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Log;

class DatabaseSeeder extends Seeder
{
    public function run(): void
    {
        $this->seedAdmin();
    }

    private function seedAdmin(): void
    {
        if (User::query()->where('role', 'superadmin')->exists()) {
            return;
        }

        $email = env('ADMIN_EMAIL');
        $password = env('ADMIN_PASSWORD');

        if (! $email || ! $password) {
            Log::warning('No superadmin exists and ADMIN_EMAIL/ADMIN_PASSWORD not set');
            return;
        }

        $admin = User::query()->create([
            'email' => $email,
            'password_hash' => Hash::make($password),
            'display_name' => 'Admin',
            'role' => 'superadmin',
        ]);

        Log::info("Created superadmin: {$email}");

        $this->migrateServerOwnership($admin);
    }

    private function migrateServerOwnership(User $admin): void
    {
        $dir = config('mcp.servers_data_dir');
        if (! $dir || ! is_dir($dir)) {
            return;
        }

        foreach (scandir($dir) as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $specPath = rtrim($dir, '/') . "/{$entry}/server.json";
            if (! is_file($specPath)) {
                continue;
            }
            if (ServerOwner::query()->where('server_name', $entry)->exists()) {
                continue;
            }
            ServerOwner::query()->create([
                'server_name' => $entry,
                'owner_id' => $admin->id,
            ]);
            Log::info("Assigned server '{$entry}' to admin");
        }
    }
}
