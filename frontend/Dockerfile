FROM node:20-slim
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh
# Clear node image ENTRYPOINT (it would otherwise run CMD as `node <args>`).
ENTRYPOINT []
# Invoke via shell so bind-mounted `docker-entrypoint.sh` does not need +x on the host.
CMD ["/bin/sh", "/app/docker-entrypoint.sh"]
