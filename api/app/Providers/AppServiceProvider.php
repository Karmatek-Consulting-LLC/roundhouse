<?php

namespace App\Providers;

use App\Services\Mcp\Codegen;
use App\Services\Mcp\DockerClient;
use App\Services\Mcp\GlobalEnvVars;
use App\Services\Mcp\McpClient;
use App\Services\Mcp\ServerPermissions;
use App\Services\Mcp\ServerService;
use App\Services\Mcp\ServerStore;
use App\Services\Mcp\TemplateEngine;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->app->singleton(DockerClient::class);
        $this->app->singleton(GlobalEnvVars::class);
        $this->app->singleton(Codegen::class);
        $this->app->singleton(McpClient::class);
        $this->app->singleton(ServerPermissions::class);
        $this->app->singleton(ServerService::class);

        $this->app->singleton(TemplateEngine::class, function ($app) {
            return new TemplateEngine(
                templatesDir: (string) realpath((string) config('mcp.templates_dir')) ?: (string) config('mcp.templates_dir'),
                serversDir: $this->resolveWritableDir((string) config('mcp.servers_data_dir')),
            );
        });

        $this->app->singleton(ServerStore::class, function () {
            return new ServerStore(
                baseDir: $this->resolveWritableDir((string) config('mcp.servers_data_dir')),
            );
        });
    }

    public function boot(): void
    {
        //
    }

    private function resolveWritableDir(string $path): string
    {
        if (! is_dir($path)) {
            @mkdir($path, 0755, true);
        }
        return $path;
    }
}
