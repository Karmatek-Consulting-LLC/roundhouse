#!/bin/sh
# Runs as PID 1 so the SIGTERM trap fires during `docker stop`/`docker compose down`.
set -e

cd /var/www/html

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

# First-boot setup: generate APP_KEY if missing, run migrations + seeders.
if [ ! -f .env ]; then
    cp .env.example .env
fi

if [ -z "$APP_KEY" ] && ! grep -q '^APP_KEY=base64:' .env; then
    php artisan key:generate --force
fi

php artisan config:clear
php artisan migrate --force --seed

# Warm caches for production.
php artisan config:cache
php artisan route:cache

# Make sure runtime dirs are writable (volumes may reset perms).
chown -R www-data:www-data storage bootstrap/cache 2>/dev/null || true

# --- Lifecycle cleanup ---
# On shutdown, tear down every container/service this platform spawned so
# `docker compose down` doesn't leak managed MCP servers.
child=""
shutdown() {
    echo "[entrypoint] shutdown signal received - cleaning up managed MCP servers..."
    php artisan mcp:cleanup-managed 2>&1 || true
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
