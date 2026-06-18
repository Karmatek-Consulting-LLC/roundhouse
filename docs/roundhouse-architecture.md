# Roundhouse — Architecture (Docker Swarm)

Self-hosted MCP server hosting on Docker Swarm, fronted by the customer's edge Traefik.
TLS is terminated at the edge; the embedded Traefik speaks HTTP only.

```mermaid
flowchart LR
  client["MCP Clients & Browsers"]

  subgraph edge["Customer Edge"]
    ctraefik["Upstream Traefik<br/>reverse proxy · TLS termination<br/>:443 → :80"]
  end

  subgraph stack["Roundhouse · Docker Swarm stack (overlay: roundhouse_roundhouse-network)"]
    direction LR
    etraefik["Traefik v3.6 (embedded)<br/>entrypoint web :80<br/>swarm + file providers"]
    api["platform-api<br/>FastAPI control plane + SPA<br/>:8000"]
    pg[("PostgreSQL 16<br/>vol: pgdata")]
    spt["docker-socket-proxy<br/>traefik · read-only"]
    spa["docker-socket-proxy<br/>api · POST + BUILD"]
    engine["Docker Engine<br/>Swarm manager<br/>/var/run/docker.sock"]
    mcp1["MCP server · github<br/>/s/github/mcp"]
    mcp2["MCP server · jira<br/>/s/jira/mcp"]
    mcp3["MCP server · …<br/>/s/{name}/mcp"]
  end

  %% data plane (requests)
  client -- "HTTPS :443" --> ctraefik
  ctraefik -- "HTTP :80 · shared public net" --> etraefik
  etraefik -- "Host() && !/s/ → :8000" --> api
  etraefik -- "/s/{name}/mcp" --> mcp1
  etraefik --> mcp2
  etraefik --> mcp3
  api -- "SQL" --> pg

  %% control plane (management)
  etraefik -. "service discovery" .-> spt
  api -. "Docker API: build / create / scale" .-> spa
  spa -.-> engine
  spt -.-> engine
  engine -. "runs server tasks" .-> mcp1
  api -. "writes dynamic routes" .-> etraefik

  classDef cp fill:#fff,stroke:#c2703d,stroke-width:2px;
  classDef db fill:#e6f2f2,stroke:#2f7a7a,color:#235e5e;
  class api cp;
  class pg db;
```

### Traffic flow
1. **Client → edge.** Client hits `https://roundhouse.example.com`; the upstream Traefik terminates TLS at `:443`.
2. **Edge → embedded Traefik.** Forwards plain HTTP to the stack's Traefik `:80` over the shared `public` overlay network.
3. **UI/API.** `Host(…) && !PathPrefix(/s/)` routes to `platform-api:8000`.
4. **MCP traffic.** `/s/{name}/mcp` routes straight to the matching MCP server service.
5. **Provisioning.** platform-api builds images and creates/scales MCP services via the scoped socket-proxy (POST+BUILD).
6. **Discovery & state.** Embedded Traefik discovers services via the read-only socket-proxy; platform-api stores metadata in Postgres and writes dynamic routes to a shared volume.
