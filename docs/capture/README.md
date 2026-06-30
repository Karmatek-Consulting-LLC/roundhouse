# Re-capturing user-guide screenshots

The screenshots in [`docs/user-guide.md`](../user-guide.md) are produced by
the two scripts in this directory, driven against a running Roundhouse
stack.

> **Screenshots are not committed.** `docs/screenshots/` is gitignored — it's
> a reproducible build artifact, kept out of the repo so clones don't pull
> ~22 MB of binaries. Regenerate it with the fast path below before building
> the docs site. `website/build-docs.mjs` will refuse to run (with a pointer
> back here) if the screenshots it references are missing — the published site
> must never ship "screenshot pending" placeholders.

> **Publishing? Just run `./website/deploy.sh`.** It runs this entire pipeline
> for you — seed → capture (both themes) → restore → strict build → `wrangler
> pages deploy` — and is fail-fast, so a broken capture never reaches
> production. The manual steps below are for iterating on the capture itself.

The fast path:

```bash
# 1) Stash the user's real servers, then seed everything the docs need
#    (Taggart servers, users, teams, traffic to populate the charts).
python3 docs/capture/seed_demo.py full

# 2) Drive headless Chromium through every documented route, in dark
#    and light themes.
node docs/capture/capture.mjs --theme both

# 3) Re-import the previously hidden real servers.
python3 docs/capture/seed_demo.py restore
```

Output lands in `docs/screenshots/{dark,light}/`.

## Sub-commands

`seed_demo.py` is split into sub-commands so the docs build can run them in
order, and so each is debuggable on its own.

| Command       | Effect |
|---------------|--------|
| `seed`        | Create the seven Taggart Transcontinental demo servers and redeploy them so their seeded primitives register. Idempotent — prior demo servers (including the legacy Atlas cast) are deleted first. |
| `users`       | Create the Taggart staff accounts (Dagny Taggart, Eddie Willers, James Taggart, Owen Kellogg, Gwen Ives) and three department teams (Operations, Maintenance of Way, Revenue Service) with members. Retires the legacy Atlas users/teams. Idempotent. |
| `traffic`     | Call every tool/resource/prompt on each running Taggart server via the platform's invoke API so the dashboard, per-server usage tabs, and "Top servers by calls" chart all have real samples. |
| `hide-real`   | Export every server in `REAL_SERVERS` (defaults to `audit-test`, `logic-monitor`) to `docs/capture/.backups/` then delete them from the stack. Run before capture so the docs don't leak real workloads. |
| `restore`     | Re-import every spec under `docs/capture/.backups/` via `/api/servers/import`. Restored files are renamed to `.json.restored` so a second run doesn't 409. |
| `cleanup`     | Delete the demo servers, teams, and users. Use after capture if you don't want the demo cast lying around. |
| `full`        | Run `hide-real → seed → users → traffic` in sequence. The standard docs-build entry point. |

> **Heads up on `hide-real`:** the export endpoint strips ciphertext from
> `secret` env vars (it's encrypted with the source instance's APP_KEY and
> useless elsewhere). After `restore`, you'll need to re-enter any secret
> env values (e.g. `LM_BEARER_TOKEN` on `logic-monitor`) and redeploy.

## Prerequisites

- A running Roundhouse stack reachable at `http://localhost:3080` (override
  with `--base`). The default admin (`admin@mcp.local` / `admin`) must
  exist; override with `--email` / `--password`.
- Node 18+ and Playwright. From this directory: `pnpm install` (or
  `npm install`), then `npx playwright install chromium`.
- Python 3 with `curl` on `PATH` — the seed script shells out to curl
  because Python's `urllib` trips this stack's auth middleware in a way we
  never tracked down; `curl` works identically.

## Demo server cast

One railroad — Taggart Transcontinental — and the internal MCP servers its
departments would actually run. Every server keeps a specific docs role:

| Server | Mode | Role in the docs |
|--------|------|------------------|
| `dispatch`          | structured | Flagship server — covers every editor tab (tools, resources, prompts, env, auth, assets, usage). |
| `track-maintenance` | structured | Pip-package and resource-template examples. |
| `crew-scheduling`   | structured | `LOG_LEVEL=DEBUG` set, so the Logs-tab level dropdown is exercised. Heaviest traffic profile → busiest usage shot. |
| `signal-telemetry`  | code       | Code-mode source editor and code-mode env tab. |
| `freight-billing`   | structured | Stopped state — shows the gray badge in dashboard + servers list. |
| `yard-inventory`    | structured | Stopped, more env-var variety. |
| `motive-power`      | structured | Round-out the dashboard fleet view. |

## Re-running after a UI change

`capture.mjs` is selector-loose (text patterns and roles rather than CSS
selectors) so it survives most reskins. If a step starts failing, edit its
entry in `STEPS`:

- `wait.selector` — element to wait for before screenshotting.
- `wait.delay` — extra settling time (ms) for animations / chart paints.
- `waitUntil` — defaults to `networkidle`; use `domcontentloaded` for
  routes that open a persistent SSE stream (logs).
- `fullPage` — defaults to true; set false for pages whose scroll height
  would make the shot unwieldy (audit log, logs tail).

To capture only one theme during iteration:

```bash
node docs/capture/capture.mjs --theme dark
```
