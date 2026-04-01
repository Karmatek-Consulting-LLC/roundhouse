#!/bin/sh
set -e
pnpm install --frozen-lockfile
exec pnpm dev --host --port 5173
