#!/usr/bin/env bash
#
# deploy.sh — the ONE supported way to publish roundhousemcp.com.
#
# Screenshots in the docs are gitignored, regenerated artifacts. This script
# regenerates them from a live stack, then builds and publishes, so the site
# can never ship stale or "screenshot pending" placeholder images. Every step
# is fail-fast: if capture or the (strict) build fails, nothing is deployed.
#
#   ./website/deploy.sh
#
# Requires a running Roundhouse stack (default http://localhost:3080; override
# with ROUNDHOUSE_BASE) and push access to the GitHub repo.
#
set -euo pipefail

BASE="${ROUNDHOUSE_BASE:-http://localhost:3080}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$1"; }

# 0) A live stack is required — capture drives a real browser against it.
step "Checking for a running stack at $BASE"
if ! curl -fsS -o /dev/null "$BASE/"; then
  echo "✗ No Roundhouse stack reachable at $BASE." >&2
  echo "  Start one (e.g. 'docker compose up -d') or set ROUNDHOUSE_BASE, then retry." >&2
  exit 1
fi

# 1) Seed the demo cast (hides real servers, seeds Taggart servers/users/traffic).
step "Seeding demo data"
python3 docs/capture/seed_demo.py full --base "$BASE"

# 2) Capture every documented route in both themes. On any failure we still
#    restore the real servers (trap) before exiting non-zero.
restore() { step "Restoring real servers"; python3 docs/capture/seed_demo.py restore --base "$BASE" || true; }
trap restore EXIT
step "Capturing screenshots (dark + light)"
node docs/capture/capture.mjs --theme both --base "$BASE"

# 3) Restore now (and clear the trap so it doesn't run twice).
trap - EXIT
restore

# 4) Build the static docs site. STRICT: aborts if any referenced screenshot
#    is missing, so a broken capture can never reach production.
step "Building docs site (strict)"
node website/build-docs.mjs

# 5) Publish to GitHub Pages (production) by pushing website/ to the gh-pages
#    branch. GitHub Pages serves roundhousemcp.com with a Let's Encrypt cert —
#    some customer networks don't trust the Google Trust CA that Cloudflare's
#    free tier uses. -t includes dotfiles (.nojekyll); --no-history force-pushes
#    a single commit so the ~22MB of regenerated screenshots don't pile up in
#    branch history.
step "Deploying to GitHub Pages"
npx --yes gh-pages -d website -t --no-history \
  -m "Publish roundhousemcp.com ($(git rev-parse --short HEAD))"

step "Done — https://roundhousemcp.com"
