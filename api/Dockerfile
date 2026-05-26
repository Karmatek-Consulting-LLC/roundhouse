# ---- Stage 1: Build the React frontend ----
FROM node:22-alpine AS frontend-build

COPY api/docker/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN apk add --no-check-certificate --no-cache ca-certificates && update-ca-certificates
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc* ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime

COPY api/docker/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN cat /usr/local/share/ca-certificates/corp-ca.crt >> /etc/ssl/certs/ca-certificates.crt \
    && update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
        netcat-openbsd \
        tar \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# ruff is shelled out from PythonFormatter to canonicalize generated server.py.
RUN pip install --no-cache-dir ruff

WORKDIR /app

COPY api/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir .

COPY api/app /app/app
COPY templates /app/templates
COPY --from=frontend-build /build/dist /app/public/frontend

COPY api/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Writable dirs for spec storage + Traefik dynamic config.
RUN mkdir -p /var/lib/mcp-platform/servers /var/lib/mcp-platform/traefik-dynamic

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
