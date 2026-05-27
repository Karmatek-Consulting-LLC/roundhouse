import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { useTheme } from "@/hooks/use-theme";

interface CreateServerDialogProps {
  onCreated: () => void;
}

type CreateMethod = "structured" | "code" | "git" | "import";

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

export function CreateServerDialog({ onCreated }: CreateServerDialogProps) {
  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState<CreateMethod>("structured");

  // Shared
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [replicas, setReplicas] = useState<number | "">("");

  // Code-first
  const [source, setSource] = useState(CODE_MODE_STARTER);

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
    if (!open) return;
    void api.getServerReplicaLimits().then(setLimits).catch(() => setLimits(null));
  }, [open]);

  useEffect(() => {
    if (method !== "code") return;
    if (!name) return;
    setSource((prev) => prev.replace(/FastMCP\("[^"]*"\)/, `FastMCP("${name}")`));
  }, [name, method]);

  function reset() {
    setMethod("structured");
    setName("");
    setDescription("");
    setReplicas("");
    setSource(CODE_MODE_STARTER);
    setGitUrl("");
    setGitRef("");
    setImportJson("");
    setError(null);
  }

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
      setOpen(false);
      reset();
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create server");
    } finally {
      setCreating(false);
    }
  }

  const submitDisabled =
    creating ||
    (method === "structured" && !name) ||
    (method === "code" && (!name || !source.trim())) ||
    (method === "git" && (!name || !gitUrl.trim())) ||
    (method === "import" && !importJson.trim());

  const subtitle = {
    structured: "Create an empty server, then add tools, resources, and prompts.",
    code: 'Paste a full FastMCP server.py - the platform handles Docker, Traefik, and env.',
    git: "Clone a git repo containing server.py and deploy it directly.",
    import: "Restore a server from a previously exported JSON spec.",
  }[method];

  const wide = method === "code" || method === "import";

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>Create Server</Button>
      </DialogTrigger>
      <DialogContent className={wide ? "sm:max-w-3xl" : "sm:max-w-md"}>
        <DialogHeader>
          <DialogTitle>Create MCP Server</DialogTitle>
          <DialogDescription>{subtitle}</DialogDescription>
        </DialogHeader>

        <Tabs value={method} onValueChange={(v) => setMethod(v as CreateMethod)}>
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="structured">Structured</TabsTrigger>
            <TabsTrigger value="code">Code-first</TabsTrigger>
            <TabsTrigger value="git">From Git</TabsTrigger>
            <TabsTrigger value="import">Import</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="grid gap-4 py-4">
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

          {(method === "structured" || method === "code" || method === "git") && (
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
                  height="340px"
                  theme={resolvedTheme === "dark" ? "dark" : "light"}
                  extensions={[python()]}
                  onChange={(v) => setSource(v)}
                  basicSetup={{ lineNumbers: true, foldGutter: false }}
                />
              </div>
            </div>
          )}

          {method === "git" && (
            <>
              <div className="grid gap-2">
                <Label htmlFor="git-url">Git URL</Label>
                <p className="text-xs text-muted-foreground">
                  Public HTTPS or SSH URL. The repo must contain <code>server.py</code> at
                  its root. If a <code>Dockerfile</code> isn't included, the platform
                  generates one.
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
                a bare spec object. Runtime tokens are not transferred - mint new ones
                after import.
              </p>
              <Textarea
                id="import-json"
                className="min-h-[260px] font-mono text-xs"
                placeholder='{"version": 1, "spec": { ... }}'
                value={importJson}
                onChange={(e) => setImportJson(e.target.value)}
              />
            </div>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button onClick={handleCreate} disabled={submitDisabled}>
            {creating ? "Creating..." : method === "import" ? "Import" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
