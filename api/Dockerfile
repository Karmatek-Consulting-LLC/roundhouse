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

# ---- Stage 3: Runtime - PHP-FPM + nginx + supervisor ----
FROM php:8.4-fpm-alpine AS runtime

# Install OS + PHP extensions (pdo_pgsql for Postgres; ctype/tokenizer already bundled).
RUN apk add --no-cache \
        nginx supervisor tar netcat-openbsd \
        postgresql-libs postgresql-dev \
        libzip-dev oniguruma-dev \
    && docker-php-ext-install pdo pdo_pgsql bcmath zip opcache \
    && apk del postgresql-dev \
    && rm -rf /var/cache/apk/*

WORKDIR /var/www/html

# Copy composer deps from the vendor stage.
COPY --from=vendor /app/vendor /var/www/html/vendor

# Copy application code.
COPY api/ /var/www/html/

# Copy built frontend + templates (shared with the MCP codegen pipeline).
COPY --from=frontend-build /build/dist /var/www/html/public/frontend
COPY templates/ /var/www/html/templates/

# Merge the autoloader now that app code is in place.
RUN composer dump-autoload --optimize --no-dev --working-dir=/var/www/html

# Writable dirs for Laravel + MCP server specs + Traefik dynamic config.
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

# Config files.
COPY api/docker/nginx.conf /etc/nginx/nginx.conf
COPY api/docker/default.conf /etc/nginx/http.d/default.conf
COPY api/docker/php-fpm.conf /usr/local/etc/php-fpm.d/zz-mcp.conf
COPY api/docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY api/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 9000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
