# MCP Platform

A self-hosted platform for building, deploying, and managing [Model Context
Protocol](https://modelcontextprotocol.io) servers. Write a tool or resource
in the web editor, hit deploy, and a containerized FastMCP server is live at a
stable URL ready for Claude Desktop, Claude Code, or any MCP client.

![Demo](docs/demo.gif)
<!-- TODO(marty): 30-second screencap of create → add primitive → deploy → invoke from Claude -->

## Why

MCP is a great spec, but the deployment story is mostly "scripts on a
developer's laptop." This platform sits one level up:

- **Codegen + deploy**: define primitives in a structured form (or paste raw
  Python), the platform generates `server.py` + `Dockerfile`, builds the
  image, and runs the container behind Traefik routing.
- **Centralized**: one URL per server, shared with your team, no laptop
  required.
- **Auth that works on day one**: scoped bearer tokens out of the box. Swap
  for OIDC later when you need it.
- **Lifecycle visible**: status, logs, redeploys, scopes, tokens — all in the
  same editor.

## Screenshots

![Server editor](docs/server-editor.png)
<!-- TODO(marty): the IDE-style editor with primitives nav on the left -->

![Primitive form](docs/primitive-form.png)
<!-- TODO(marty): the primitive form with CodeMirror -->

## Quick start

Requires Docker and Docker Compose. Clone, copy the env template, bring it
up:

```bash
git clone https://github.com/Karmatek-Consulting-LLC/mcp-platform.git
cd mcp-platform
cp .env.example .env
docker compose up -d
```

When the API logs say `Application startup complete`, open
**http://localhost:3080** and sign in with `admin@mcp.local` / `admin`.

Tear down:

```bash
docker compose down       # preserve database + spec files
docker compose down -v    # wipe everything
```

## What's in the box

- **FastAPI** backend (Python 3.12) talking directly to the Docker socket.
- **React + Vite** frontend, IDE-style editor with deep-link selection.
- **Postgres** for users, teams, scopes, runtime tokens, audit data.
- **Traefik** front door for `/api/*` (platform) and `/s/{name}/*` (each
  spawned MCP server).
- **Two server modes**:
  - *Structured*: define tools, resources, prompts in forms; the platform
    generates `server.py`.
  - *Code-first*: paste a full `server.py`, platform handles packaging +
    routing only.

## Architecture

```
   ┌──────────────┐   HTTP   ┌────────────────────┐
   │   Browser    │ ───────► │   Traefik :3080    │
   │  (React SPA) │ ◄─────── │      (router)      │
   └──────────────┘          └────────────────────┘
                                  │           │
                       /api/*     │           │  /s/{server}/mcp
                                  ▼           ▼
              ┌────────────────────────┐   ┌──────────────────────┐
              │   platform-api         │   │  spawned MCP servers │
              │   FastAPI              │   │  mcp-{name}:8000     │
              │                        │   │  (FastMCP containers)│
              │  • Codegen (Python)    │   │                      │
              │  • Docker client       │   │  Built + deployed    │
              │  • MCP JSON-RPC client │   │  via /var/run/       │
              └──────────┬─────────────┘   │  docker.sock         │
                         │                  └──────────────────────┘
                         ▼
                  ┌─────────────┐
                  │  postgres   │
                  └─────────────┘
```

The platform never proxies MCP traffic for normal use — Traefik routes the
client straight to the spawned container. The platform-api only talks to MCP
servers internally for the "Test" buttons in the UI.

## Connecting Claude

Each deployed server gets a stable URL like `http://localhost:3080/s/my-server/mcp`.

For **Claude Desktop**, add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "url": "http://localhost:3080/s/my-server/mcp",
      "headers": {
        "Authorization": "Bearer <token from server's Auth panel>"
      }
    }
  }
}
```

For **Claude Code**: `claude mcp add my-server --url http://... --header "Authorization: Bearer ..."`.

## Configuration

Everything reads from environment variables — see [`.env.example`](.env.example).
Key knobs:

| Var | What |
|---|---|
| `APP_KEY` | `base64:<32 random bytes>` for runtime-token encryption at rest. |
| `MCP_BASE_URL` | What URL clients see for spawned servers. Set this when deploying. |
| `MCP_DOCKER_HOST` | `/var/run/docker.sock` (default) or `tcp://socket-proxy:2375` for hardened Swarm setups. |
| `MAX_MCP_SERVER_REPLICAS` | Per-server replica cap (Swarm only). |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | First-boot seed user; ignored once a user exists. |

## Deployment modes

- **Local / single-host**: `docker-compose.yml`. Single Docker daemon, Traefik
  on the same socket. Ideal for trying it out or hosting for a small team.
- **Docker Swarm**: `docker-stack-lab.yml`. Multi-node, scoped socket proxies,
  designed to sit behind a cluster ingress that terminates TLS.

## Development

Backend (`api/`) is FastAPI + SQLAlchemy. Hot-reload is on by default in
`docker-compose.yml` via uvicorn `--reload`.

Frontend (`frontend/`) is React + Vite. The dev server inside the
`frontend` container hot-reloads.

```bash
# Tail API logs
docker compose logs -f platform-api

# Tail frontend logs
docker compose logs -f frontend

# Run the API outside Docker (point at the dockerized Postgres)
cd api
python -m venv .venv && source .venv/bin/activate
pip install -e .
DB_HOST=localhost uvicorn app.main:app --reload
```

## Behind a corporate TLS-inspecting proxy

If your network MITMs TLS for outbound requests during image builds, drop the
proxy's CA bundle at `api/docker/corp-ca.crt` before `docker compose build`.
The Dockerfiles pick it up via a `[t]` glob; the file is gitignored so it
can't be committed by accident. In any other environment, leave it absent and
the build skips that step.

## License

<!-- TODO(marty): decide AGPL-3.0 (defensive, open-core friendly) vs Apache-2.0 (permissive) -->
TBD
