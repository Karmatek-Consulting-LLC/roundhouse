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
# On shutdown, tear down every container/service this platform spawned so
# `docker compose down` doesn't leak managed MCP servers.
child=""
shutdown() {
    echo "[dev-entrypoint] shutdown signal received - cleaning up managed MCP servers..."
    python -m app.cleanup 2>&1 || true
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
