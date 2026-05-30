<div align="center">

<img src="docs/logo.svg" alt="Roundhouse" width="120" />

# Roundhouse

**A self-hosted home for your [MCP](https://modelcontextprotocol.io) servers.**

Write a tool in the browser, hit deploy, and a containerized FastMCP server is
live at a stable URL — ready for Claude Desktop, Claude Code, or any MCP client.

[![Built with FastAPI](https://img.shields.io/badge/backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React + Vite](https://img.shields.io/badge/frontend-React%20%2B%20Vite-61dafb?logo=react&logoColor=white)](https://vitejs.dev)
[![Docker](https://img.shields.io/badge/runtime-Docker-2496ed?logo=docker&logoColor=white)](https://www.docker.com)
[![FastMCP](https://img.shields.io/badge/MCP-FastMCP-7c3aed)](https://github.com/jlowin/fastmcp)
[![License](https://img.shields.io/badge/license-TBD-lightgrey)](#license)

<a href="docs/demo.gif"><img src="docs/demo.gif" alt="Roundhouse demo" /></a>
<sub><i>30-second screencap: create → add primitive → deploy → invoke from Claude</i></sub>

</div>

---

## Why Roundhouse?

MCP is a great spec, but the deployment story today is mostly *"scripts on a
developer's laptop."* Roundhouse sits one level up — a small platform that
turns "I wrote a tool" into "my team can use it from Claude."

|  | Roundhouse |
|---|---|
| 🛠 &nbsp; **Codegen + deploy** | Define primitives in a structured form (or paste raw Python). Roundhouse generates `server.py` + `Dockerfile`, builds the image, runs the container behind Traefik. |
| 🌐 &nbsp; **Centralized URLs** | One stable URL per server, shared across your team. No laptop required, no port juggling. |
| 🔐 &nbsp; **Auth on day one** | Scoped bearer tokens out of the box. Swap for OIDC when you outgrow them. |
| 🔭 &nbsp; **Lifecycle visible** | Status, logs, redeploys, scopes, tokens — all in the same editor. |
| 🧩 &nbsp; **Two authoring modes** | *Structured* (forms → codegen) for quick tools. *Code-first* (paste a `server.py`) when you need full control. |
| 🐳 &nbsp; **Single host or Swarm** | Run it on a laptop with `docker compose`, or on a Swarm cluster with scoped socket proxies. |

---

## Screenshots

<table>
<tr>
<td width="50%"><img src="docs/server-editor.png" alt="Server editor" /></td>
<td width="50%"><img src="docs/primitive-form.png" alt="Primitive form" /></td>
</tr>
<tr>
<td align="center"><sub>IDE-style editor — primitives on the left, code on the right.</sub></td>
<td align="center"><sub>Define a tool's inputs, outputs, and body. Codegen handles the rest.</sub></td>
</tr>
</table>

---

## Quick start

> Requires **Docker** and **Docker Compose**.

```bash
git clone https://github.com/Karmatek-Consulting-LLC/mcp-platform.git
cd mcp-platform
cp .env.example .env
docker compose up -d
```

When the API logs print `Application startup complete`, open
**<http://localhost:3080>** and sign in with `admin@mcp.local` / `admin`.

```bash
docker compose down        # preserve database + spec files
docker compose down -v     # wipe everything
```

---

## Connect Claude

Every deployed server gets a stable URL like
`http://localhost:3080/s/my-server/mcp`.

<details open>
<summary><b>Claude Desktop</b></summary>

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "url": "http://localhost:3080/s/my-server/mcp",
      "headers": {
        "Authorization": "Bearer <token from the server's Auth panel>"
      }
    }
  }
}
```
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add my-server \
  --url    http://localhost:3080/s/my-server/mcp \
  --header "Authorization: Bearer <token>"
```
</details>

---

## Architecture

```mermaid
flowchart LR
    Browser["Browser<br/><sub>React SPA</sub>"]
    Traefik["Traefik :3080<br/><sub>router</sub>"]
    API["platform-api<br/><sub>FastAPI · Python 3.12</sub><br/>• Codegen<br/>• Docker client<br/>• MCP JSON-RPC"]
    MCP["spawned MCP servers<br/><sub>mcp-{name}:8000</sub><br/><sub>FastMCP containers</sub>"]
    DB[("Postgres")]

    Browser <-->|HTTP| Traefik
    Traefik -->|/api/*| API
    Traefik -->|/s/{server}/mcp| MCP
    API --> DB
    API -.->|docker.sock<br/>build + deploy| MCP

    classDef platform fill:#fef3ec,stroke:#c2693a,color:#1a1a1a
    classDef spawned  fill:#f3eafe,stroke:#7c3aed,color:#1a1a1a
    classDef infra    fill:#eef4ff,stroke:#4f6bed,color:#1a1a1a
    class API,Traefik,DB platform
    class MCP spawned
    class Browser infra
```

Traefik routes MCP clients **straight to the spawned container** — the
platform never proxies MCP traffic on the hot path. The platform-api only
speaks MCP internally, to power the *Test* buttons in the UI.

---

## What's in the box

- **FastAPI** backend (Python 3.12) talking directly to the Docker socket
- **React + Vite** frontend with an IDE-style editor and deep-link selection
- **Postgres** for users, teams, scopes, runtime tokens, and audit data
- **Traefik** front door routing `/api/*` (platform) and `/s/{name}/*` (servers)
- **Alembic** migrations baked into startup
- Two server modes: *structured codegen* and *code-first*

---

## Configuration

Everything reads from environment variables — see
[`.env.example`](.env.example) for the full list. The knobs you'll actually
touch:

| Variable | Purpose |
|---|---|
| `APP_KEY` | `base64:<32 random bytes>` — encrypts runtime tokens at rest. Generate with `printf 'base64:%s' "$(openssl rand -base64 32)"`. |
| `MCP_BASE_URL` | The URL clients see for spawned servers. Set this when deploying past localhost. |
| `MCP_DOCKER_HOST` | `/var/run/docker.sock` (default) or `tcp://socket-proxy:2375` for hardened Swarm setups. |
| `MAX_MCP_SERVER_REPLICAS` | Per-server replica cap (Swarm only). |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | First-boot seed user. Ignored once a user exists. |

---

## Deployment modes

| Mode | File | Best for |
|---|---|---|
| **Local / single-host** | [`docker-compose.yml`](docker-compose.yml) | Trying it out, small teams, hosting on one box. Single Docker daemon, Traefik on the same socket. |
| **Docker Swarm** | [`docker-stack-lab.yml`](docker-stack-lab.yml) | Multi-node, scoped socket proxies. Designed to sit behind a cluster ingress that terminates TLS. |

---

## Development

The backend (`api/`) is FastAPI + SQLAlchemy. The frontend (`frontend/`) is
React + Vite. Hot-reload is on by default in `docker-compose.yml`.

```bash
# Tail logs
docker compose logs -f platform-api
docker compose logs -f frontend

# Run the API outside Docker, pointed at the dockerized Postgres
cd api
python -m venv .venv && source .venv/bin/activate
pip install -e .
DB_HOST=localhost uvicorn app.main:app --reload
```

<details>
<summary><b>Behind a corporate TLS-inspecting proxy</b></summary>

If your network MITMs outbound TLS during image builds, drop the proxy's CA
bundle at `api/docker/corp-ca.crt` before running `docker compose build`.
The Dockerfiles pick it up via a `[t]` glob; the file is gitignored so it
can't be committed by accident. In any other environment, leave it absent
and the build skips that step.
</details>

---

## Roadmap

- ⏱ &nbsp; Per-server rate limiting & request middleware hooks
- 📈 &nbsp; Built-in metrics + health page
- 🔒 &nbsp; Secret-grade environment variables (sealed at rest, never echoed)
- 🧱 &nbsp; Resource caps per server (CPU / memory / restart policy)
- 🆔 &nbsp; OIDC / SSO for the admin UI

See [`docs/`](docs/) for design notes as they land.

---

## License

<!-- TODO(marty): decide AGPL-3.0 (defensive, open-core friendly) vs Apache-2.0 (permissive) -->
TBD — coming soon.

---

<div align="center">
<sub>Built with care by <a href="https://karmatek.io">Karmatek</a>.<br/>Roundhouse is the home you wish your MCP servers had.</sub>
</div>
