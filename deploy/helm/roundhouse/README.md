# Roundhouse Helm chart

Deploys Roundhouse (FastAPI backend + React frontend + optional Postgres) into a Kubernetes cluster and grants it the RBAC it needs to spawn MCP server workloads on the same cluster.

## What this chart does NOT do

- **Install Traefik.** It assumes Traefik is already running cluster-side and that the `traefik.io/v1alpha1` `IngressRoute` and `Middleware` CRDs are registered. Install Traefik first via the upstream chart.
- **Provision your registry.** The chart needs an existing registry the cluster can pull from. By default (`imageBuilder.kind=kaniko`) it also needs a `kubernetes.io/dockerconfigjson` Secret it can mount into Kaniko Jobs so they can push.
- **Manage runtime registry creds for spawned servers.** Configure `docker_registry` (and credentials) inside Platform Settings after the UI is up. If the registry is private, pass an `imagePullSecret` via `--set kubernetes.imagePullSecret=<name>`.

## Prerequisites

| Requirement | Why |
|---|---|
| Kubernetes ≥ 1.26 | `apps/v1` Deployment scale subresource + modern RBAC |
| Traefik installed in-cluster, CRDs registered | The platform creates `IngressRoute`/`Middleware` per spawned MCP server |
| A Docker registry the cluster can pull from | Spawned MCP servers run from images this platform builds and pushes |
| A `kubernetes.io/dockerconfigjson` Secret with push creds for that registry | Mounted into Kaniko Jobs at `/kaniko/.docker/config.json` |
| Storage class supporting RWO | Postgres + the api's server-data PVC. Kaniko Jobs are pinned to the api pod's node via `NODE_NAME` so RWO is sufficient. |

## Install

First, create the registry-push Secret in the chart's namespace:

```bash
kubectl create namespace roundhouse
kubectl -n roundhouse create secret docker-registry mcp-registry-creds \
  --docker-server=registry.example.com \
  --docker-username=mcp-bot \
  --docker-password=$REGISTRY_TOKEN
```

Then install:

```bash
helm install mcp deploy/helm/roundhouse \
  --namespace roundhouse \
  --set baseUrl=https://mcp.example.com \
  --set traefik.hostname=mcp.example.com \
  --set imageBuilder.kaniko.registrySecret=mcp-registry-creds \
  --set postgres.auth.password=$(openssl rand -hex 16) \
  --set admin.password=$(openssl rand -hex 12)
```

### Using an out-of-cluster Docker host instead of Kaniko

Set `imageBuilder.kind=docker` and provide `imageBuilder.host`. The api pod will then talk to that endpoint for builds (the legacy path), and the Kaniko Job/RBAC are skipped.

```bash
helm install mcp deploy/helm/roundhouse \
  --namespace roundhouse --create-namespace \
  --set imageBuilder.kind=docker \
  --set imageBuilder.host=tcp://buildkitd.build.svc:1234 \
  --set baseUrl=https://mcp.example.com \
  --set traefik.hostname=mcp.example.com
```

The chart creates two namespaces' worth of resources:

- `roundhouse` (release namespace): api Deployment + Service + PVC, frontend Deployment + Service, ConfigMap, Secret, optional Postgres StatefulSet, ServiceAccount, IngressRoute.
- `mcp-servers` (configurable via `workloadsNamespace.name`): empty at install time — the platform writes a Deployment + Service + IngressRoute + Middleware here for every MCP server users create through the UI. A `Role` + `RoleBinding` granting the api `ServiceAccount` permission to manage those resources is also created here.

## Required values

| Key | Description |
|---|---|
| `baseUrl` | External URL the platform reports to MCP clients (must match the IngressRoute host) |
| `imageBuilder.kaniko.registrySecret` | `kubernetes.io/dockerconfigjson` Secret used by Kaniko to push images (required when `imageBuilder.kind=kaniko`, the default) |
| `imageBuilder.host` | Required only when `imageBuilder.kind=docker` |

The install fails fast with a clear message if any of these are missing.

## Postgres: bundled or external

The bundled Postgres StatefulSet (default) is appropriate for small/internal installs. For production:

```yaml
postgres:
  enabled: false
externalDatabase:
  host: postgres.internal
  user: mcp
  database: mcp
  passwordSecret: mcp-db-password   # secret with key "password"
```

## Upgrades

`APP_KEY` is generated on first install and read back from the live Secret on subsequent renders, so `helm upgrade` won't invalidate existing encrypted data. If you rotate it manually, delete the Secret and `helm upgrade` to regenerate.

The platform's Secret is annotated `helm.sh/resource-policy: keep`, so `helm uninstall` leaves it behind — delete it manually if you want a clean slate.

## RBAC

`templates/rbac.yaml` grants the api `ServiceAccount` a Role in `workloadsNamespace.name` covering:

- `apps/deployments`, `apps/deployments/scale` — create/scale/delete spawned MCP server Deployments
- `services` — create the per-server ClusterIP Service
- `pods`, `pods/log` — fetch logs for the UI's "View Logs" tab
- `traefik.io/ingressroutes`, `traefik.io/middlewares` — wire `/s/{name}` routing per server

If you change the workloads namespace after install, re-run `helm upgrade` so the RoleBinding moves with it.

## Caveats

- **Frontend pod runs the Vite dev server image.** Lab's FastAPI backend doesn't mount static files, and there's no static-serve frontend image yet, so the chart deploys `frontend/Dockerfile` as-is (port 5173, vite dev). Functional but heavier than a static build. Two follow-ups would let us collapse to a single api pod: (a) add a `StaticFiles` mount in `app/main.py` against the `public/frontend` dir the api Dockerfile already populates, or (b) publish a static-serve frontend image and point `image.frontend` at it.
- **Build context delivery uses node-affinity.** The api PVC is RWO; the Kaniko Job is pinned to the api pod's node via `NODE_NAME` (Downward API). If you use an RWX storage class you can drop the pin in a follow-up.
- Helm chart version is `0.1.0` — interface is subject to change while the k8s backend matures.
