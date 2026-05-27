# ---- Stage 1: Build the React frontend ----
FROM node:22-alpine AS frontend-build

# Optional corp CA: drop a PEM at api/docker/corp-ca.crt before building when
# the build host's egress goes through a TLS-inspecting proxy. The [t] glob
# lets the COPY silently skip when the file is absent (lab / dev case).
COPY api/docker/corp-ca.cr[t] /tmp/build-ca/
RUN if [ -s /tmp/build-ca/corp-ca.crt ]; then \
        apk add --no-cache ca-certificates && \
        cp /tmp/build-ca/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt && \
        update-ca-certificates; \
    fi && rm -rf /tmp/build-ca
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

# Optional corp CA - same convention as the frontend stage.
COPY api/docker/corp-ca.cr[t] /tmp/build-ca/
RUN if [ -s /tmp/build-ca/corp-ca.crt ]; then \
        cp /tmp/build-ca/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt && \
        cat /tmp/build-ca/corp-ca.crt >> /etc/ssl/certs/ca-certificates.crt && \
        update-ca-certificates; \
    fi && rm -rf /tmp/build-ca
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
        netcat-openbsd \
        tar \
        git \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# ruff is shelled out from PythonFormatter to canonicalize generated server.py.
RUN pip install --no-cache-dir ruff

WORKDIR /app

COPY api/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir .

COPY api/app /app/app
COPY api/alembic /app/alembic
COPY api/alembic.ini /app/alembic.ini
COPY templates /app/templates
COPY --from=frontend-build /build/dist /app/public/frontend

COPY api/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Writable dirs for spec storage + Traefik dynamic config.
RUN mkdir -p /var/lib/mcp-platform/servers /var/lib/mcp-platform/traefik-dynamic

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
