# syntax=docker/dockerfile:1.7

# ---- Stage 1: Build the React frontend ----
FROM node:22-alpine AS frontend-build
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc* ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---- Stage 2: Install PHP dependencies ----
FROM composer:2.9 AS vendor
WORKDIR /app
COPY api/composer.json api/composer.lock ./
RUN composer install \
    --no-dev --no-interaction --no-scripts --prefer-dist \
    --no-progress --optimize-autoloader

# ---- Stage 3: Runtime - PHP + Swoole, serving via Laravel Octane ----
FROM php:8.4-cli-alpine AS runtime

# Swoole needs linux-headers + openssl-dev to compile; postgresql-dev for pdo_pgsql.
# We keep the build deps long enough to pecl-install swoole, then drop them.
RUN apk add --no-cache \
        tar netcat-openbsd \
        postgresql-libs libzip oniguruma brotli-libs libstdc++ \
    && apk add --no-cache --virtual .build-deps \
        $PHPIZE_DEPS linux-headers openssl-dev \
        postgresql-dev libzip-dev oniguruma-dev brotli-dev \
    && docker-php-ext-install pdo pdo_pgsql bcmath zip opcache \
    && pecl install swoole \
    && docker-php-ext-enable swoole \
    && apk del .build-deps \
    && rm -rf /var/cache/apk/* /tmp/pear

# Composer binary is needed in runtime to dump-autoload after app code lands.
COPY --from=vendor /usr/bin/composer /usr/local/bin/composer

WORKDIR /var/www/html

# Copy composer deps from the vendor stage.
COPY --from=vendor /app/vendor /var/www/html/vendor

# Copy application code.
COPY api/ /var/www/html/

# Copy built frontend + templates (shared with the MCP codegen pipeline).
COPY --from=frontend-build /build/dist /var/www/html/public/frontend
COPY templates/ /var/www/html/templates/

# Writable dirs for Laravel + MCP server specs + Traefik dynamic config.
# bootstrap/cache must exist before dump-autoload's package:discover runs.
RUN mkdir -p \
        storage/app/servers \
        storage/app/traefik/dynamic \
        storage/app/traefik/certs \
        storage/framework/cache \
        storage/framework/sessions \
        storage/framework/views \
        storage/logs \
        bootstrap/cache \
    && chown -R www-data:www-data storage bootstrap/cache

# Merge the autoloader now that app code is in place.
RUN composer dump-autoload --optimize --no-dev --working-dir=/var/www/html

COPY api/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["php", "artisan", "octane:start", "--server=swoole", "--host=0.0.0.0", "--port=8000"]
