import { useEffect, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import type { ServersOutletContext } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { useTheme } from "@/hooks/use-theme";
import {
  ArrowLeft,
  Boxes,
  FileCode,
  GitBranch,
  Globe,
  Plus,
  Upload,
  X,
} from "lucide-react";

type CreateMethod = "structured" | "code" | "remote" | "git" | "import";

interface RemoteHeaderRow {
  header: string;
  value: string;
}

interface MethodMeta {
  value: CreateMethod;
  label: string;
  icon: typeof Boxes;
  blurb: string;
  subtitle: string;
}

const METHODS: MethodMeta[] = [
  {
    value: "structured",
    label: "Structured",
    icon: Boxes,
    blurb: "Build tools, resources, and prompts in the editor.",
    subtitle: "Create an empty server, then add tools, resources, and prompts.",
  },
  {
    value: "code",
    label: "Code-first",
    icon: FileCode,
    blurb: "Paste a full FastMCP server.py.",
    subtitle:
      "Paste a full FastMCP server.py — the platform handles Docker, Traefik, and env.",
  },
  {
    value: "remote",
    label: "Remote",
    icon: Globe,
    blurb: "Proxy an external MCP server with your access control.",
    subtitle:
      "Proxy an external MCP server. Roundhouse discovers its tools and layers your access control, metrics, and logging.",
  },
  {
    value: "git",
    label: "From Git",
    icon: GitBranch,
    blurb: "Clone a repo containing server.py.",
    subtitle:
      "Clone a git repo containing server.py. It imports unconfigured — set env vars, then deploy.",
  },
  {
    value: "import",
    label: "Import",
    icon: Upload,
    blurb: "Restore from an exported JSON spec.",
    subtitle: "Restore a server from a previously exported JSON spec.",
  },
];

const CODE_MODE_STARTER = `from fastmcp import FastMCP

mcp = FastMCP("server-name")

# Define your tools / resources / prompts here, e.g.:
# @mcp.tool()
# def greet(name: str) -> str:
#     return f"Hello, {name}"


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
        json_response=True,
    )
`;

export function CreateServerPage() {
  const navigate = useNavigate();
  const { refresh } = useOutletContext<ServersOutletContext>();
  const [method, setMethod] = useState<CreateMethod>("structured");

  // Shared
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [replicas, setReplicas] = useState<number | "">("");

  // Code-first
  const [source, setSource] = useState(CODE_MODE_STARTER);

  // Remote proxy
  const [remoteUrl, setRemoteUrl] = useState("");
  const [remoteHeaders, setRemoteHeaders] = useState<RemoteHeaderRow[]>([
    { header: "Authorization", value: "" },
  ]);

  // Git deploy
  const [gitUrl, setGitUrl] = useState("");
  const [gitRef, setGitRef] = useState("");

  // Import JSON
  const [importJson, setImportJson] = useState("");

  const [limits, setLimits] = useState<{
    default_mcp_server_replicas: number;
    max_mcp_server_replicas: number;
    docker_swarm_mode: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    void api.getServerReplicaLimits().then(setLimits).catch(() => setLimits(null));
  }, []);

  useEffect(() => {
    if (method !== "code") return;
    if (!name) return;
    setSource((prev) => prev.replace(/FastMCP\("[^"]*"\)/, `FastMCP("${name}")`));
  }, [name, method]);

  function effectiveReplicas() {
    return replicas === "" || replicas === limits?.default_mcp_server_replicas
      ? undefined
      : Number(replicas);
  }

  async function handleCreate() {
    setError(null);
    setCreating(true);
    try {
      const rep = effectiveReplicas();
      if (method === "structured") {
        await api.createServer({
          name,
          description,
          ...(rep !== undefined ? { replicas: rep } : {}),
        });
      } else if (method === "code") {
        await api.createServer({
          name,
          description,
          mode: "code",
          source,
          ...(rep !== undefined ? { replicas: rep } : {}),
        });
      } else if (method === "remote") {
        await api.createServer({
          name,
          description,
          mode: "remote",
          remote_url: remoteUrl.trim(),
          remote_headers: remoteHeaders
            .filter((h) => h.header.trim() && h.value.trim())
            .map((h) => ({ header: h.header.trim(), value: h.value })),
          ...(rep !== undefined ? { replicas: rep } : {}),
        });
      } else if (method === "git") {
        await api.deployFromGit({
          name,
          git_url: gitUrl,
          ...(gitRef ? { ref: gitRef } : {}),
          ...(description ? { description } : {}),
          ...(rep !== undefined ? { replicas: rep } : {}),
        });
      } else if (method === "import") {
        const parsed = JSON.parse(importJson);
        // Accept either the full export envelope or a bare spec.
        const spec =
          parsed && typeof parsed === "object" && "spec" in parsed
            ? (parsed as { spec: Record<string, unknown> }).spec
            : (parsed as Record<string, unknown>);
        await api.importServer({
          spec,
          ...(name ? { name_override: name } : {}),
        });
      }
      // A git import lands as not_deployed; a remote server lands deployed with
      // its toolset discovered but every tool locked (default-deny). Either way,
      // take the operator straight to the editor — to fill env + deploy (git) or
      // to assign scopes to the discovered tools (remote). Other methods return
      // to the server list.
      const goToEditor = method === "git" || method === "remote" ? name : null;
      refresh();
      navigate(
        goToEditor ? `/servers/${encodeURIComponent(goToEditor)}` : "/servers",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create server");
      setCreating(false);
    }
  }

  const submitDisabled =
    creating ||
    (method === "structured" && !name) ||
    (method === "code" && (!name || !source.trim())) ||
    (method === "remote" && (!name || !remoteUrl.trim())) ||
    (method === "git" && (!name || !gitUrl.trim())) ||
    (method === "import" && !importJson.trim());

  const active = METHODS.find((m) => m.value === method)!;

  return (
    <div className="flex flex-col -mx-4 sm:-mx-6 lg:-mx-8">
      {/* Header */}
      <div className="flex items-center gap-3 border-b px-4 sm:px-6 lg:px-8 py-3">
        <Button variant="ghost" size="sm" onClick={() => navigate("/servers")}>
          <ArrowLeft className="mr-1 h-4 w-4" /> Servers
        </Button>
        <h2 className="text-lg font-semibold">Create MCP Server</h2>
      </div>

      <div className="mx-auto w-full max-w-4xl px-4 sm:px-6 lg:px-8 py-6">
        {/* Method picker */}
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {METHODS.map((m) => {
            const Icon = m.icon;
            const selected = m.value === method;
            return (
              <button
                key={m.value}
                type="button"
                onClick={() => setMethod(m.value)}
                aria-pressed={selected}
                className={`flex flex-col items-start gap-2 rounded-lg border p-3 text-left transition-colors ${
                  selected
                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                    : "hover:border-foreground/30 hover:bg-muted/50"
                }`}
              >
                <Icon
                  className={`h-5 w-5 ${
                    selected ? "text-primary" : "text-muted-foreground"
                  }`}
                />
                <span className="text-sm font-medium">{m.label}</span>
                <span className="text-xs text-muted-foreground">{m.blurb}</span>
              </button>
            );
          })}
        </div>

        <p className="mt-4 text-sm text-muted-foreground">{active.subtitle}</p>

        {/* Form */}
        <div className="mt-6 grid gap-5">
          {method !== "import" && (
            <div className="grid gap-2">
              <Label htmlFor="server-name">Server Name</Label>
              <Input
                id="server-name"
                placeholder="my-server"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          )}
          {method === "import" && (
            <div className="grid gap-2">
              <Label htmlFor="server-name-override">Name override (optional)</Label>
              <p className="text-xs text-muted-foreground">
                Leave blank to use the name in the exported spec; provide one to clone
                under a new name.
              </p>
              <Input
                id="server-name-override"
                placeholder="(use name from spec)"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          )}

          {method !== "import" && (
            <div className="grid gap-2">
              <Label htmlFor="server-replicas">Replicas (Swarm)</Label>
              <p className="text-xs text-muted-foreground">
                Desired tasks when the service is running. Leave empty for the platform default (
                {limits?.default_mcp_server_replicas ?? "…"}){limits && !limits.docker_swarm_mode
                  ? ". Single-container mode: only one instance runs regardless."
                  : "."}
              </p>
              <Input
                id="server-replicas"
                type="number"
                min={1}
                max={limits?.max_mcp_server_replicas ?? 32}
                placeholder={limits ? String(limits.default_mcp_server_replicas) : "default"}
                value={replicas}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "") setReplicas("");
                  else setReplicas(Math.max(1, parseInt(v, 10) || 1));
                }}
              />
            </div>
          )}

          {(method === "structured" || method === "code" || method === "remote" || method === "git") && (
            <div className="grid gap-2">
              <Label htmlFor="server-desc">Description</Label>
              <p className="text-xs text-muted-foreground">
                Passed to LLMs as context for the server.
              </p>
              <Textarea
                id="server-desc"
                className="min-h-[80px]"
                placeholder="What this MCP server provides…"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
          )}

          {method === "code" && (
            <div className="grid gap-2">
              <Label>server.py</Label>
              <p className="text-xs text-muted-foreground">
                Must include <code>mcp = FastMCP(...)</code> and a{" "}
                <code>mcp.run(transport="streamable-http", host="0.0.0.0", port=8000,
                stateless_http=True, json_response=True)</code>{" "}
                invocation. Mode can't be changed after create.
              </p>
              <div className="rounded-md border">
                <CodeMirror
                  value={source}
                  height="420px"
                  theme={resolvedTheme === "dark" ? "dark" : "light"}
                  extensions={[python()]}
                  onChange={(v) => setSource(v)}
                  basicSetup={{ lineNumbers: true, foldGutter: false }}
                />
              </div>
            </div>
          )}

          {method === "remote" && (
            <>
              <div className="grid gap-2">
                <Label htmlFor="remote-url">Upstream MCP URL</Label>
                <p className="text-xs text-muted-foreground">
                  The remote server's streamable-HTTP endpoint, e.g.{" "}
                  <code>https://kibana.example/api/agent_builder/mcp</code>. Roundhouse
                  discovers its tools on create; every tool starts locked until you grant
                  a scope.
                </p>
                <Input
                  id="remote-url"
                  placeholder="https://host/mcp"
                  value={remoteUrl}
                  onChange={(e) => setRemoteUrl(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label>Auth / request headers</Label>
                <p className="text-xs text-muted-foreground">
                  Sent to the upstream on every call (e.g. <code>Authorization</code> ={" "}
                  <code>ApiKey &lt;key&gt;</code>). Values are stored encrypted and never
                  shown again. The caller's own token is never forwarded upstream.
                </p>
                {remoteHeaders.map((row, i) => (
                  <div key={i} className="flex gap-2">
                    <Input
                      className="w-1/3"
                      placeholder="Header"
                      value={row.header}
                      onChange={(e) =>
                        setRemoteHeaders((rows) =>
                          rows.map((r, j) => (j === i ? { ...r, header: e.target.value } : r)),
                        )
                      }
                    />
                    <Input
                      className="flex-1"
                      type="password"
                      placeholder="Value (secret)"
                      value={row.value}
                      onChange={(e) =>
                        setRemoteHeaders((rows) =>
                          rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)),
                        )
                      }
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={remoteHeaders.length === 1}
                      onClick={() =>
                        setRemoteHeaders((rows) => rows.filter((_, j) => j !== i))
                      }
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="justify-self-start"
                  onClick={() =>
                    setRemoteHeaders((rows) => [...rows, { header: "", value: "" }])
                  }
                >
                  <Plus className="mr-1 h-4 w-4" /> Add header
                </Button>
              </div>
            </>
          )}

          {method === "git" && (
            <>
              <div className="grid gap-2">
                <Label htmlFor="git-url">Git URL</Label>
                <p className="text-xs text-muted-foreground">
                  Public HTTPS or SSH URL. The repo must contain <code>server.py</code> at
                  its root. Declare dependencies and required env vars in a{" "}
                  <code>roundhouse.json</code> manifest — the platform builds the
                  Dockerfile, so you don't ship one.
                </p>
                <Input
                  id="git-url"
                  placeholder="https://github.com/owner/repo.git"
                  value={gitUrl}
                  onChange={(e) => setGitUrl(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="git-ref">Branch / tag (optional)</Label>
                <Input
                  id="git-ref"
                  placeholder="main"
                  value={gitRef}
                  onChange={(e) => setGitRef(e.target.value)}
                />
              </div>
            </>
          )}

          {method === "import" && (
            <div className="grid gap-2">
              <Label htmlFor="import-json">Exported spec JSON</Label>
              <p className="text-xs text-muted-foreground">
                Paste the JSON returned by <code>GET /api/servers/&lt;name&gt;/export</code> or
                a bare spec object. Runtime tokens are not transferred — mint new ones
                after import.
              </p>
              <Textarea
                id="import-json"
                className="min-h-[320px] font-mono text-xs"
                placeholder='{"version": 1, "spec": { ... }}'
                value={importJson}
                onChange={(e) => setImportJson(e.target.value)}
              />
            </div>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        {/* Action bar */}
        <div className="mt-8 flex items-center justify-end gap-2 border-t pt-4">
          <Button variant="outline" onClick={() => navigate("/servers")} disabled={creating}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={submitDisabled}>
            {creating ? "Creating..." : method === "import" ? "Import" : "Create"}
          </Button>
        </div>
      </div>
    </div>
  );
}
