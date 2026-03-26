#!/bin/sh
pnpm install
exec pnpm dev --host --port 5173
