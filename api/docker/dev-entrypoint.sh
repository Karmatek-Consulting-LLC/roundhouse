#!/bin/sh
# Runs as PID 1 so the SIGTERM trap fires during `docker compose down`.
set -e

cd /app

# Wait for Postgres.
if [ -n "$DB_HOST" ]; then
    echo "Waiting for database ${DB_HOST}:${DB_PORT:-5432}..."
    until nc -z "$DB_HOST" "${DB_PORT:-5432}" 2>/dev/null; do
        sleep 1
    done
fi

# --- Lifecycle cleanup ---
# By default, MCP servers persist across platform restarts - a routine
# `docker compose restart platform-api` shouldn't tear down independent
# user workloads. Set MCP_CLEANUP_ON_SHUTDOWN=true in the environment when
# you actually want a full teardown (CI scripts, `compose down -v`); the
# manual command `docker exec platform-api python -m app.cleanup` still
# works at any time.
child=""
shutdown() {
    if [ "${MCP_CLEANUP_ON_SHUTDOWN:-false}" = "true" ]; then
        echo "[dev-entrypoint] shutdown signal received - cleaning up managed MCP servers..."
        python -m app.cleanup 2>&1 || true
    else
        echo "[dev-entrypoint] shutdown signal received - leaving managed MCP servers running (set MCP_CLEANUP_ON_SHUTDOWN=true to force cleanup)."
    fi
    if [ -n "$child" ]; then
        kill -TERM "$child" 2>/dev/null || true
        wait "$child" 2>/dev/null || true
    fi
    exit 0
}
trap shutdown TERM INT

"$@" &
child=$!
wait "$child"
