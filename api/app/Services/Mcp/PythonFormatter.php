<?php

namespace App\Services\Mcp;

use Illuminate\Support\Facades\Log;

/**
 * Pipe Python source through `ruff format -` for canonical formatting.
 *
 * ruff is a native binary - no Python dependency. If it's missing or errors
 * out for any reason, format() returns the original source unchanged. We never
 * fail a codegen / deploy because of a formatter hiccup.
 */
class PythonFormatter
{
    private const BIN = 'ruff';

    public function format(string $py): string
    {
        if (trim($py) === '') {
            return $py;
        }

        $process = @proc_open(
            [self::BIN, 'format', '-'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
        );
        if (! is_resource($process)) {
            Log::warning('PythonFormatter: ruff not invocable; returning unformatted output');
            return $py;
        }

        fwrite($pipes[0], $py);
        fclose($pipes[0]);
        $out = stream_get_contents($pipes[1]);
        $err = stream_get_contents($pipes[2]);
        fclose($pipes[1]);
        fclose($pipes[2]);
        $exit = proc_close($process);

        if ($exit !== 0 || ! is_string($out) || $out === '') {
            Log::warning('PythonFormatter: ruff format failed', [
                'exit' => $exit,
                'stderr' => trim((string) $err),
            ]);
            return $py;
        }
        return $out;
    }
}
