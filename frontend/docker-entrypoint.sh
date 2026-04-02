#!/bin/sh
set -e
# Keep the content-addressable store on a normal Linux volume, not under /app (bind-mounted from macOS).
# Mixing VirtioFS + Docker volumes causes EPERM on copyfile into node_modules.
STORE_DIR=/var/cache/pnpm-store
mkdir -p "$STORE_DIR"
pnpm install --frozen-lockfile --store-dir "$STORE_DIR"
exec pnpm dev --host --port 5173
