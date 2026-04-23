<?php

namespace App\Console\Commands;

use App\Services\Mcp\DockerClient;
use Illuminate\Console\Command;

/**
 * Tear down every container/service labelled mcp-platform.managed=true.
 * Runs from the platform container's SIGTERM trap so `docker compose down` removes spawned MCP servers.
 * DB rows and spec files on disk are kept — redeploying after next boot is intentional.
 */
class CleanupManagedServers extends Command
{
    protected $signature = 'mcp:cleanup-managed
                            {--timeout=5 : Max seconds to wait for each Docker op}';

    protected $description = 'Remove all Docker containers/services this platform spawned';

    public function handle(DockerClient $docker): int
    {
        $servers = [];
        try {
            $servers = $docker->listServers();
        } catch (\Throwable $e) {
            $this->error("Failed to list managed servers: {$e->getMessage()}");
            return self::FAILURE;
        }

        if (! $servers) {
            $this->info('No managed servers running — nothing to clean up.');
            return self::SUCCESS;
        }

        $mode = $docker->swarmMode() ? 'swarm service' : 'container';
        $this->info(sprintf('Removing %d managed %s(s)...', count($servers), $mode));

        $failed = 0;
        foreach ($servers as $s) {
            $name = $s['name'] ?? '(unknown)';
            try {
                $docker->removeServer($name);
                $this->line("  ✓ {$name}");
            } catch (\Throwable $e) {
                $failed++;
                $this->line("  ✗ {$name}: {$e->getMessage()}");
            }
        }

        if ($failed > 0) {
            $this->warn("{$failed} removal(s) failed — inspect `docker ps --filter label=mcp-platform.managed=true`.");
            return self::FAILURE;
        }

        $this->info('Done.');
        return self::SUCCESS;
    }
}
