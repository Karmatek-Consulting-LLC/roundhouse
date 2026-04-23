#!/bin/sh
# Runs as PID 1 so the SIGTERM trap fires during `docker compose down`.
set -e

cd /var/www/html

# Install composer deps on first boot (vendor/ is typically on a named volume).
if [ ! -d vendor ] || [ ! -f vendor/autoload.php ]; then
    composer install --no-interaction --prefer-dist
fi

# Wait for Postgres.
if [ -n "$DB_HOST" ]; then
    echo "Waiting for database ${DB_HOST}:${DB_PORT:-5432}..."
    until nc -z "$DB_HOST" "${DB_PORT:-5432}" 2>/dev/null; do
        sleep 1
    done
fi

# Generate app key if missing (overrides empty APP_KEY from compose env).
if [ -z "$APP_KEY" ] && ! grep -q '^APP_KEY=base64:' .env 2>/dev/null; then
    if [ ! -f .env ]; then
        cp .env.example .env
    fi
    php artisan key:generate --force
fi

php artisan migrate --force --seed || true

mkdir -p storage/app/servers storage/app/traefik/dynamic storage/app/traefik/certs
chown -R www-data:www-data storage bootstrap/cache 2>/dev/null || true

# --- Lifecycle cleanup ---
child=""
shutdown() {
    echo "[dev-entrypoint] shutdown signal received — cleaning up managed MCP servers..."
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
