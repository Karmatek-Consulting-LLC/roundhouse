# Stage 1: Build frontend
FROM node:20-slim AS frontend-build
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# Stage 2: Production image
FROM python:3.12-slim
WORKDIR /app

# Install Python dependencies
COPY platform/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY platform/app/ ./app/
COPY platform/alembic.ini ./
COPY platform/alembic/ ./alembic/

# Copy built frontend
COPY --from=frontend-build /build/dist ./static/

# Create data directories
RUN mkdir -p /app/data/servers /app/traefik/dynamic /app/traefik/certs

EXPOSE 9000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
