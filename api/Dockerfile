# ---- Stage 1: Build the React frontend ----
FROM node:22-alpine AS frontend-build

# Corp CA for npm registry / package fetches
COPY api/docker/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN apk add --no-cache ca-certificates && update-ca-certificates
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc* ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---- Stage 2: Install PHP dependencies ----
FROM composer:2.9 AS vendor

# composer:2.9 is Alpine-based
COPY api/docker/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN apk add --no-cache ca-certificates && update-ca-certificates

# Tell PHP/cURL inside the composer image about the CA
RUN { \
        echo "openssl.cafile=/etc/ssl/certs/ca-certificates.crt"; \
        echo "curl.cainfo=/etc/ssl/certs/ca-certificates.crt"; \
    } > /usr/local/etc/php/conf.d/corp-ca.ini

WORKDIR /app
COPY api/composer.json api/composer.lock ./
RUN composer install \
    --no-dev --no-interaction --no-scripts --prefer-dist \
    --no-progress --optimize-autoloader

# ---- Stage 3: Runtime - FrankenPHP (Caddy + PHP) serving via Laravel Octane ----
FROM dunglas/frankenphp:1-php8.4-alpine AS runtime

# Corp CA at OS level + PHP level (FrankenPHP base is Alpine)
COPY api/docker/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN apk add --no-cache ca-certificates && update-ca-certificates

RUN { \
        echo "openssl.cafile=/etc/ssl/certs/ca-certificates.crt"; \
        echo "curl.cainfo=/etc/ssl/certs/ca-certificates.crt"; \
    } > /usr/local/etc/php/conf.d/corp-ca.ini

# Belt-and-suspenders for tools that bypass php.ini (AWS SDK, etc.)
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# install-php-extensions ships with the frankenphp image; it pulls and cleans
# up build deps automatically so the final layer stays slim.
RUN install-php-extensions pdo_pgsql bcmath zip opcache pcntl

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
CMD ["php", "artisan", "octane:start", "--server=frankenphp", "--host=0.0.0.0", "--port=8000"]
