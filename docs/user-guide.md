# Roundhouse User Guide

A visual tour of every menu, dialog, and editor surface in Roundhouse.

> Screenshots in this guide are shown in **dark theme**. A light-theme set
> lives under `docs/screenshots/light/`. See
> [`docs/capture/README.md`](capture/README.md) for how to re-capture.

---

## Sign in

Roundhouse uses email/password sign-in. The first admin is created from the
`ADMIN_EMAIL` and `ADMIN_PASSWORD` environment variables when the API boots.

![Login](screenshots/dark/01-login.png)

---

## Dashboard

The dashboard is the home view after sign-in. It surfaces fleet-wide health
(running/stopped/errored counts), recent call volume, the busiest servers,
and a rolling activity timeline.

![Dashboard](screenshots/dark/02-dashboard.png)

---

## Servers list

The full inventory of MCP servers you can see. Each row shows the server's
deploy mode (structured or code), live status, recent call count, and quick
actions.

![Servers list](screenshots/dark/03-servers-list.png)

---

## Creating a server

The **Create** button opens a dialog with four tabs.

### Structured

The default: an empty spec-managed server. Primitives, packages, env vars,
and middleware are managed through the UI; Roundhouse owns the Dockerfile.

![Create — Structured](screenshots/dark/04-create-structured.png)

### Code-first

You supply a complete `server.py` (FastMCP). Roundhouse still owns the
Dockerfile and platform middleware, but the primitive surface is hidden in
favour of a full source editor.

![Create — Code-first](screenshots/dark/05-create-code.png)

### From Git

Clone a repo that declares its dependencies in `roundhouse.json`. The
imported server registers as `not_deployed` — Roundhouse seeds the
environment variables declared by the manifest, populates pip and apt
packages, and waits for an operator to fill in secrets before the first
deploy.

![Create — From Git](screenshots/dark/06-create-from-git.png)

### Import

Paste an exported spec JSON (from another Roundhouse instance, or from
`POST /api/servers/{name}/export`) to clone a server's configuration
verbatim.

![Create — Import](screenshots/dark/07-create-import.png)

---

## The server editor

Selecting a server opens the two-pane editor. The left rail is the server's
table of contents — primitives, configuration, and operational tabs. The
right pane is whichever section you've selected.

### Overview

The overview is the editor's home base: description, replicas, resource
limits, and the lifecycle controls (Start, Stop, Redeploy, Delete, plus
**Update from Git** for repos imported via `from-git`).

![Editor — overview](screenshots/dark/10-editor-overview.png)

### Primitives

Structured servers expose **tools**, **resources**, **resource templates**,
and **prompts**. Each primitive is edited in-place: parameters, body, and
optional middleware overrides (rate limit, max concurrency).

#### Tool

![Editor — primitive (tool)](screenshots/dark/11-editor-primitive-tool.png)

#### Resource

![Editor — primitive (resource)](screenshots/dark/12-editor-primitive-resource.png)

#### Prompt

![Editor — primitive (prompt)](screenshots/dark/13-editor-primitive-prompt.png)

#### Adding a new primitive

![Editor — new primitive](screenshots/dark/14-editor-primitive-new.png)

### Imports and globals

Any free-form Python imports or module-level globals that should appear in
the generated `server.py`.

![Editor — imports](screenshots/dark/15-editor-imports.png)

### PyPI packages

Pip dependencies. They are pinned into the generated Dockerfile and
installed at build time.

![Editor — packages (pip)](screenshots/dark/16-editor-packages.png)

### APT packages

OS-level packages installed via `apt-get` in the build. Use for native
toolchains a Python wheel needs.

![Editor — apt packages](screenshots/dark/17-editor-apt.png)

### Environment variables

Per-server env vars (with optional encrypted-at-rest secrets) and global
imports. Changes save instantly; the redeploy banner surfaces because the
new value only takes effect on the next container restart.

![Editor — env vars](screenshots/dark/18-editor-env.png)

### Auth (server tokens)

Bearer tokens that callers present in the `Authorization` header. Per-token
scopes can lock individual primitives down to specific token holders.

![Editor — auth](screenshots/dark/19-editor-auth.png)

### Assets

Arbitrary files that get baked into the image under `/app/assets/`. Useful
for prompt templates, large JSON fixtures, or any read-only data your tools
reference at runtime.

![Editor — assets](screenshots/dark/20-editor-assets.png)

### Usage

Per-primitive call counts, p50/p95/p99 latency, error rate, and the busiest
client tokens. Sampled in-process by the platform middleware and surfaced
without an extra metrics backend — no Prometheus, no Grafana, no add-on
agent. Drilling into a server's usage tab is how you find latency
regressions or the one tool that's getting hammered.

![Editor — usage (Taggart)](screenshots/dark/21-editor-usage.png)

A busier server (Galt Engine, with several thousand calls across three
tools) makes the chart variety obvious:

![Editor — usage (Galt Engine)](screenshots/dark/21a-editor-usage-busy.png)

### Logs

Streams stdout/stderr from the server container via Server-Sent Events.

For spec-based servers, the **Level** dropdown writes the `LOG_LEVEL`
environment variable on the spec; the platform middleware reads it and
configures stdlib logging accordingly. At `DEBUG`, the middleware also
emits a start record (with arguments) for every tool/resource/prompt call.
Failed calls promote the end-of-call record to `WARNING`. The dropdown is
hidden for code-mode servers, since they own their own logging surface.

![Editor — logs](screenshots/dark/22-editor-logs.png)

### Source (code mode)

For servers in **code mode**, the editor replaces the primitive surface
with a full `server.py` editor (CodeMirror, Python syntax highlighting).
The platform still controls the Dockerfile.

![Editor — source (code mode)](screenshots/dark/30-editor-source.png)

Code-mode servers still expose every operational tab — env vars, logs,
usage — so the operator surface is unchanged.

![Editor — code-mode env](screenshots/dark/31-editor-source-env.png)

### Stopped server

A stopped server keeps its spec on disk; restarting it reuses the cached
image without a rebuild.

![Editor — stopped](screenshots/dark/40-editor-stopped.png)

---

## Platform administration

Roundhouse ships with a small set of platform admin views.

### Platform settings

Host configuration: external hostname, Docker registry credentials, custom
CA bundle for outbound TLS, and platform-wide env defaults that get
imported into spec-based servers.

![Platform settings](screenshots/dark/50-settings.png)

### Users

User accounts with role assignment. The seeded demo includes a handful of
operators alongside the platform admin; superadmins can reset passwords
and revoke access from this page.

![Users](screenshots/dark/51-users.png)

### Teams

Teams group users into shared-access bundles. A team can own one or more
servers, and any team member inherits access to those servers without
needing per-server token grants. The seed creates three teams that mirror
the demo servers' industries — Taggart Operations, Rearden Industries, and
Galt's Gulch — each with its own member roster.

![Teams](screenshots/dark/52-teams.png)

### Audit log

Every state-mutating action — server create/delete, env-var change, token
rotation — is recorded with the acting user, target, and a structured
detail payload.

![Audit log](screenshots/dark/53-audit.png)
