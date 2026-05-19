# MCP Server Primitive Storage — Why Tools Disappear

## TL;DR

The MCP server containers are **not** the source of truth for their own tools. The
primitives (tools, resources, prompts, code, env vars) live as a JSON file on the
`platform-api` container's filesystem, mounted from a Docker **local** volume.
In Swarm on the lab, that "local" volume is per‑node, and `platform-api` has no
placement constraint — so the moment the service reschedules to a different node
it gets a brand‑new empty volume and every spec appears to vanish.

Plan: implement **A** (pin `platform-api`/`traefik` to a single labeled node) now,
then **C** (move spec storage into Postgres) as the durable fix.

---

## Where the primitive code is actually stored

The MCP server containers themselves are build‑and‑forget: `Codegen::writeBuildContext()`
renders `server.py` + `Dockerfile` into a build context, the image is built, the
container runs that baked‑in `server.py`, and that's it. The running container is
**never** introspected by the platform to discover its tools.

The real source of truth is the **ServerSpec JSON** persisted by `ServerStore`:

```9:21:api/app/Services/Mcp/ServerStore.php
class ServerStore
{
    public function __construct(private readonly string $baseDir) {}

    public function save(ServerSpec $spec): void
    {
        $dir = $this->serverDir($spec->name);
        if (! is_dir($dir)) {
            mkdir($dir, 0755, true);
        }
        $json = json_encode($spec->toArray(), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
        file_put_contents($this->specPath($spec->name), $json);
    }
```

On the `platform-api` container, each server lives at:

```
/var/www/html/storage/app/servers/{server_name}/server.json   ← spec (tools, resources, prompts, code, env)
/var/www/html/storage/app/servers/{server_name}/server.py     ← last rendered file
/var/www/html/storage/app/servers/{server_name}/Dockerfile    ← last rendered file
```

That whole tree is `MCP_SERVERS_DATA_DIR`, declared in `config/mcp.php`:

```27:27:api/config/mcp.php
    'servers_data_dir' => env('MCP_SERVERS_DATA_DIR', storage_path('app/servers')),
```

When the UI lists a server's tools, the controller does `store->load($name)` →
reads `server.json` → returns `spec.primitives`. The running MCP container is
never read back for tool definitions — it's just executing the frozen `server.py`
from build time.

`ServerController` confirms `server.json` is the read/write surface for every
spec operation:

- `store->load($name)` — list/show/edit endpoints
- `store->save($spec)` — create/update/primitive‑edit endpoints

So if `server.json` is missing, the API has no idea what tools the server has,
even if the container is still happily serving them.

## Why it goes missing on the lab

In `docker-compose.yml` (dev) the directory is on a named volume that survives
container restarts on the single host:

```46:46:docker-compose.yml
      - server-data:/var/www/html/storage/app/servers
```

And in `docker-stack-lab.yml` (Swarm) it's also a named volume:

```119:122:docker-stack-lab.yml
    volumes:
      - server-data:/var/www/html/storage/app/servers
      - traefik-dynamic:/var/www/html/storage/app/traefik/dynamic
      - traefik-certs:/var/www/html/storage/app/traefik/certs
```

…but in Swarm a "named volume" with no driver is a **local** volume on whichever
node the task happens to land on. Look at the stack: `postgres` is pinned
(`node.labels.db == true`), the docker‑socket‑proxies are pinned to managers, but
`platform-api` has **no placement constraint and no node‑pinning for its data
volume**. Same for `traefik` / `traefik-dynamic` / `traefik-certs`.

Result: every time `platform-api` (or the whole stack) reschedules and lands on a
different Swarm node, it mounts a brand‑new empty `server-data` local volume on
that node. The database still has the `ServerOwner` rows, the MCP
containers/services may still be running from previously‑built images on other
nodes, but `ServerStore::load()` returns `null` because `server.json` simply
doesn't exist on the new node's local volume. The UI then shows the server as
"empty" (no tools/resources/prompts).

Symptoms that match this:

- Tools "disappear" after a redeploy, node reboot, or `docker service update`.
- The MCP endpoint may still work for a while (the old image keeps serving), but
  you can no longer see or edit the primitives.
- Editing/redeploying writes a new (empty) spec on top, which is why people
  often think their data is gone after they click around.
- `traefik-dynamic` and `traefik-certs` have the same problem — uploaded certs
  and dynamic config also vanish on reschedule.

## How to fix

The spec dir (and ideally the Traefik dynamic/cert dirs) needs to be on storage
that's stable across nodes. Three options, in increasing order of effort:

### Option A — quick fix: pin `platform-api` and Traefik to a single node

Add a placement constraint on a labeled node, same pattern already used for
Postgres. Cheapest, no infra change. Trade‑off: no HA for the API, but the API
doesn't horizontally scale anyway in this design (single writer to that on‑disk
store).

```yaml
  platform-api:
    # ...
    deploy:
      placement:
        constraints:
          - node.labels.platform == true
      # ...
  traefik:
    # ...
    deploy:
      placement:
        constraints:
          - node.labels.platform == true
```

Then on the manager:

```bash
docker node update --label-add platform=true <node>
```

Pick a node that already has the populated `mcp-platform_server-data` volume so
no data is lost (see "Recovery" below).

### Option B — put the data on shared storage

Replace the local named volume with an NFS / CIFS / cluster‑FS backed volume
(or a bind mount to a path that's already a shared mount on every node):

```yaml
volumes:
  server-data:
    driver: local
    driver_opts:
      type: nfs
      o: addr=nfs.host,nfsvers=4,rw
      device: ":/exports/mcp-platform/server-data"
```

Do the same for `traefik-dynamic` and `traefik-certs`. This is the proper fix if
you ever want to move `platform-api` off a single node. Skipped for now because
we don't want to introduce NFS just for this.

### Option C — make the DB the source of truth (durable fix)

The cleanest long‑term fix. Persist `ServerSpec` to Postgres (already wired up
for `ServerOwner`, users, teams, settings), and write `server.py`/`Dockerfile`
to a transient build context only at build time. Then no Swarm volume gymnastics
are needed and a stateless `platform-api` becomes possible.

`ServerStore` is a thin enough abstraction that swapping its backend wouldn't
ripple far — every caller already goes through `store->load()` / `store->save()`
/ `store->listAll()` / `store->delete()` / `store->serverDir()`.

Rough shape:

1. New `server_specs` table:
   - `server_name` (PK, FK to `server_owners.server_name`)
   - `spec` JSONB (the full `ServerSpec::toArray()` payload)
   - `updated_at` timestamps
2. New `DatabaseServerStore` implementing the same surface as the current
   `ServerStore`:
   - `load($name)`: `select spec from server_specs where server_name = ?`
   - `save($spec)`: upsert
   - `listAll()`: `select all`
   - `delete($name)`: `delete from server_specs` + remove transient build dir
   - `serverDir($name)`: still needed by `Codegen::writeBuildContext()` —
     return an ephemeral path under `storage/app/build/{name}` (it only needs to
     live long enough for the Docker build to read it; can be wiped after).
3. Bind `ServerStore` to `DatabaseServerStore` in the service container so
   `ServerService` doesn't have to change.
4. One‑shot migration command: walk `MCP_SERVERS_DATA_DIR`, load each
   `server.json` with the existing file‑backed store, write it via the new
   DB‑backed store. Idempotent. Run once on the pinned node from Option A
   before retiring the volume dependency.
5. Once all envs are migrated, the `server-data` volume can be dropped from the
   stack file entirely; `traefik-dynamic` / `traefik-certs` still need attention
   separately (those are read by Traefik, not the API, so they can't move into
   the API's DB — Option A's pinning is the right answer for them, or Option B
   later).

Two design notes worth getting right when we do C:

- `ServerSpec` has a `source` field used in code mode. Make sure the JSONB
  column is large enough and indexed only on `server_name` — don't try to index
  inside the JSON.
- `Codegen::writeBuildContext()` writes the rendered files next to the spec
  today. After C, those files become purely transient build artifacts — make
  sure nothing else reads them back. Quick grep target: any reader of
  `serverDir($name) . '/server.py'` outside the build path is a smell.

## Recovery for current "empty" servers

If specs have already vanished, they're gone unless an old volume still has
them. Per Swarm node:

```bash
docker volume ls | grep server-data
docker run --rm -v mcp-platform_server-data:/d alpine ls /d
```

Find the node whose volume still has the JSON files. Pin `platform-api` there
via Option A **before** the next reschedule loses it for good. If multiple
nodes have partial data, the cheap merge is to `docker cp` the directories
together onto the chosen node's volume before pinning.

## Sequencing

1. **Now**: Option A. Label a chosen node `platform=true`, add the placement
   constraint to `platform-api` and `traefik` in `docker-stack-lab.yml`, redeploy.
   Confirm `traefik-dynamic` / `traefik-certs` / `server-data` all live on that
   node going forward.
2. **Later**: Option C. New table + `DatabaseServerStore` + migration command.
   After it's running cleanly, drop `server-data` from the stack and remove the
   filesystem fallback path from `ServerStore`. Traefik volumes still need
   Option A (or B) — that's not in scope for C.
