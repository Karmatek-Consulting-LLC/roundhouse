FROM node:20-slim
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY docker-entrypoint.sh ./
EXPOSE 5173
CMD ["./docker-entrypoint.sh"]
