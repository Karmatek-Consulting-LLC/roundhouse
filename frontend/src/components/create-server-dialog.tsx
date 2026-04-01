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

interface CreateServerDialogProps {
  onCreated: () => void;
}

export function CreateServerDialog({ onCreated }: CreateServerDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [replicas, setReplicas] = useState<number | "">("");
  const [limits, setLimits] = useState<{
    default_mcp_server_replicas: number;
    max_mcp_server_replicas: number;
    docker_swarm_mode: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!open) return;
    void api.getServerReplicaLimits().then(setLimits).catch(() => setLimits(null));
  }, [open]);

  function reset() {
    setName("");
    setDescription("");
    setReplicas("");
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
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create MCP Server</DialogTitle>
          <DialogDescription>
            Create an empty server, then add tools, resources, and prompts.
          </DialogDescription>
        </DialogHeader>

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

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button onClick={handleCreate} disabled={!name || creating}>
            {creating ? "Creating..." : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
