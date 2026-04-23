import { useEffect, useState } from "react";
import { api, type ServerMode } from "@/lib/api";
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
  const [mode, setMode] = useState<ServerMode>("structured");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [replicas, setReplicas] = useState<number | "">("");
  const [source, setSource] = useState(CODE_MODE_STARTER);
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

  // Swap "server-name" placeholder in the starter as they type, so first-timers
  // don't have to remember to update it.
  useEffect(() => {
    if (mode !== "code") return;
    if (!name) return;
    setSource((prev) => prev.replace(/FastMCP\("[^"]*"\)/, `FastMCP("${name}")`));
  }, [name, mode]);

  function reset() {
    setMode("structured");
    setName("");
    setDescription("");
    setReplicas("");
    setSource(CODE_MODE_STARTER);
    setError(null);
  }

  async function handleCreate() {
    setError(null);
    setCreating(true);
    try {
      const rep =
        replicas === "" || replicas === limits?.default_mcp_server_replicas
          ? undefined
          : Number(replicas);
      await api.createServer({
        name,
        description,
        ...(rep !== undefined ? { replicas: rep } : {}),
        ...(mode === "code" ? { mode: "code", source } : {}),
      });
      setOpen(false);
      reset();
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create server");
    } finally {
      setCreating(false);
    }
  }

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
      <DialogContent className={mode === "code" ? "sm:max-w-3xl" : "sm:max-w-md"}>
        <DialogHeader>
          <DialogTitle>Create MCP Server</DialogTitle>
          <DialogDescription>
            {mode === "structured"
              ? "Create an empty server, then add tools, resources, and prompts."
              : "Paste a full FastMCP server.py — the platform handles Docker, Traefik, and env."}
          </DialogDescription>
        </DialogHeader>

        <Tabs value={mode} onValueChange={(v) => setMode(v as ServerMode)}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="structured">Structured</TabsTrigger>
            <TabsTrigger value="code">Code-first</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="server-name">Server Name</Label>
            <Input
              id="server-name"
              placeholder="my-server"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="server-replicas">Replicas (Swarm)</Label>
            <p className="text-xs text-muted-foreground">
              Desired tasks when the service is running. Leave empty to use the platform default (
              {limits?.default_mcp_server_replicas ?? "…"}){limits && !limits.docker_swarm_mode
                ? ". Single-container mode: only one instance runs regardless."
                : "."}
            </p>
            <Input
              id="server-replicas"
              type="number"
              min={1}
              max={limits?.max_mcp_server_replicas ?? 32}
              placeholder={
                limits
                  ? String(limits.default_mcp_server_replicas)
                  : "default"
              }
              value={replicas}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "") setReplicas("");
                else setReplicas(Math.max(1, parseInt(v, 10) || 1));
              }}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="server-desc">Description</Label>
            <p className="text-xs text-muted-foreground">
              Describe the server's purpose, capabilities, and intended use. This is
              passed to LLMs as context for the entire server.
            </p>
            <Textarea
              id="server-desc"
              className="min-h-[120px]"
              placeholder="This MCP server provides tools for managing network devices, including configuration retrieval, interface monitoring, and firmware updates."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {mode === "code" && (
            <div className="grid gap-2">
              <Label>server.py</Label>
              <p className="text-xs text-muted-foreground">
                Must include <code>mcp = FastMCP(...)</code> and a{" "}
                <code>mcp.run(transport="streamable-http", host="0.0.0.0", port=8000,
                stateless_http=True, json_response=True)</code>{" "}
                invocation. Mode can't be changed after create — delete and recreate to switch.
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

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button onClick={handleCreate} disabled={!name || creating || (mode === "code" && !source.trim())}>
            {creating ? "Creating..." : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
