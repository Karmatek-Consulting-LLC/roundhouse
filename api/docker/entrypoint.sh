#!/bin/sh
# Runs as PID 1 so the SIGTERM trap fires during `docker stop`/`docker compose down`.
set -e

cd /app

# Hydrate env from *_FILE secrets (standard Docker Swarm pattern).
# e.g. APP_KEY_FILE=/run/secrets/mcp_app_key → export APP_KEY="$(cat /run/secrets/mcp_app_key)"
for secret_var in APP_KEY ADMIN_EMAIL ADMIN_PASSWORD DB_PASSWORD; do
    file_var="${secret_var}_FILE"
    eval "file_val=\${${file_var}:-}"
    if [ -n "$file_val" ] && [ -r "$file_val" ]; then
        eval "export ${secret_var}=\"\$(cat \"$file_val\")\""
    fi
done

# Wait for Postgres to be reachable before doing anything DB-heavy.
if [ -n "$DB_HOST" ]; then
    echo "Waiting for database ${DB_HOST}:${DB_PORT:-5432}..."
    until nc -z "$DB_HOST" "${DB_PORT:-5432}" 2>/dev/null; do
        sleep 1
    done
fi

# --- Lifecycle cleanup ---
# MCP servers persist across platform restarts by default. Set
# MCP_CLEANUP_ON_SHUTDOWN=true in the environment when you actually want a
# full teardown (CI scripts, `compose down -v`). Manual cleanup remains
# available via `docker exec ... python -m app.cleanup`.
child=""
shutdown() {
    if [ "${MCP_CLEANUP_ON_SHUTDOWN:-false}" = "true" ]; then
        echo "[entrypoint] shutdown signal received - cleaning up managed MCP servers..."
        python -m app.cleanup 2>&1 || true
    else
        echo "[entrypoint] shutdown signal received - leaving managed MCP servers running (set MCP_CLEANUP_ON_SHUTDOWN=true to force cleanup)."
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
