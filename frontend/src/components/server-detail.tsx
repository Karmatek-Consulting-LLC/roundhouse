import { useCallback, useEffect, useState } from "react";
import { api, type Primitive, type Server, type ServerEnvConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { AddPrimitiveDialog } from "@/components/add-primitive-dialog";
import { TestPrimitiveDialog } from "@/components/test-primitive-dialog";
import { ImportsEditor } from "@/components/imports-editor";
import { PackageManager } from "@/components/package-manager";
import {
  ServerEnvBindingsEditor,
  type ServerEnvBindings,
} from "@/components/server-env-bindings-editor";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, Check, Loader2, Pencil, RefreshCw, Rocket, Trash2 } from "lucide-react";

interface ServerDetailProps {
  serverName: string;
  onBack: () => void;
}

const kindLabels: Record<string, string> = {
  tool: "Tool",
  resource: "Resource",
  resource_template: "Resource Template",
  prompt: "Prompt",
};

const kindColors: Record<string, string> = {
  tool: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  resource: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
  resource_template: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400",
  prompt: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
};

export function ServerDetail({ serverName, onBack }: ServerDetailProps) {
  const [server, setServer] = useState<Server | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  // Description editing
  const [editingDesc, setEditingDesc] = useState(false);
  const [localDesc, setLocalDesc] = useState("");
  const [savingDesc, setSavingDesc] = useState(false);

  // Local config state (not yet deployed)
  const [localImports, setLocalImports] = useState<string[]>([]);
  const [localPackages, setLocalPackages] = useState<string[]>([]);
  const [localEnvBindings, setLocalEnvBindings] = useState<ServerEnvBindings>({
    env_global_imports: [],
    env_vars: [],
  });
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  const [logs, setLogs] = useState<string | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);

  const [replicaLimits, setReplicaLimits] = useState<{
    max_mcp_server_replicas: number;
    docker_swarm_mode: boolean;
  } | null>(null);
  const [localReplicas, setLocalReplicas] = useState(1);
  const [savingReplicas, setSavingReplicas] = useState(false);
  const [redeploying, setRedeploying] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getServer(serverName);
      setServer(data);
      setLocalDesc(data.description ?? "");
      setLocalImports(data.imports ?? []);
      setLocalPackages(data.pip_packages ?? []);
      setLocalEnvBindings({
        env_global_imports: data.env_global_imports ?? [],
        env_vars: data.env_vars ?? [],
      });
    } finally {
      setLoading(false);
    }
  }, [serverName]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    void api
      .getServerReplicaLimits()
      .then((l) =>
        setReplicaLimits({
          max_mcp_server_replicas: l.max_mcp_server_replicas,
          docker_swarm_mode: l.docker_swarm_mode,
        }),
      )
      .catch(() => setReplicaLimits(null));
  }, []);

  useEffect(() => {
    if (server) setLocalReplicas(server.replicas_desired);
  }, [server]);

  const savedEnv: ServerEnvConfig = {
    env_global_imports: server?.env_global_imports ?? [],
    env_vars: server?.env_vars ?? [],
  };

  const configDirty =
    server !== null &&
    (JSON.stringify(localImports) !== JSON.stringify(server.imports ?? []) ||
      JSON.stringify(localPackages) !== JSON.stringify(server.pip_packages ?? []) ||
      JSON.stringify(localEnvBindings) !== JSON.stringify(savedEnv));

  async function handleDeploy() {
    setDeploying(true);
    setDeployError(null);
    try {
      const filteredLocals = localEnvBindings.env_vars.filter((v) => v.name.trim());
      const cleanImports = localImports.filter((i) => i.trim());
      await api.deployConfig(serverName, cleanImports, localPackages, {
        env_global_imports: localEnvBindings.env_global_imports,
        env_vars: filteredLocals,
      });
      await refresh();
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : "Deploy failed");
    } finally {
      setDeploying(false);
    }
  }

  async function handleRedeploy() {
    setRedeploying(true);
    setDeployError(null);
    try {
      await api.redeployServer(serverName);
      await refresh();
      void loadLogs();
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : "Redeploy failed");
    } finally {
      setRedeploying(false);
    }
  }

  async function loadLogs() {
    setLogsLoading(true);
    setLogsError(null);
    try {
      const text = await api.getServerLogs(serverName, 400);
      setLogs(text.trim() ? text : "(no log lines yet)");
    } catch (e) {
      setLogsError(e instanceof Error ? e.message : "Failed to load logs");
      setLogs(null);
    } finally {
      setLogsLoading(false);
    }
  }

  async function handleDeletePrimitive(p: Primitive) {
    setDeleting(p.name);
    try {
      await api.deletePrimitive(serverName, p.name);
      refresh();
    } finally {
      setDeleting(null);
    }
  }

  if (loading) {
    return <div className="py-12 text-center text-muted-foreground">Loading...</div>;
  }

  if (!server) {
    return <div className="py-12 text-center text-muted-foreground">Server not found</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back
        </Button>
      </div>

      <Tabs defaultValue="details" className="w-full">
        <TabsList className="grid w-full max-w-md grid-cols-2 sm:inline-flex sm:w-auto">
          <TabsTrigger value="details">Details</TabsTrigger>
          <TabsTrigger value="runtime">Deployment &amp; logs</TabsTrigger>
        </TabsList>

        <TabsContent value="details" className="mt-6 space-y-6">
      {(server.status === "not_deployed" || server.status === "unknown") && (
        <div
          className={
            server.status === "unknown"
              ? "rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive"
              : "rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-950 dark:text-amber-100"
          }
        >
          {server.status === "unknown" ? (
            <>
              <strong>Docker status unavailable.</strong> The platform could not read this server from
              Docker. Check that the Docker socket is reachable and try again.
            </>
          ) : (
            <>
              <strong>Not deployed to Docker.</strong> This server is registered but has no running
              service or container. Use <strong>Deploy Changes</strong> below after editing configuration,
              or fix the deployment from the Deployment &amp; logs tab.
            </>
          )}
        </div>
      )}
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-semibold tracking-tight">{server.name}</h2>
            <StatusBadge status={server.status} />
          </div>
          <p className="mt-2">
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono break-all">
              {server.url}
            </code>
          </p>
          <p className="mt-3 text-xs text-muted-foreground">
            Use this <strong>full URL</strong> in MCP Inspector with transport <strong>Streamable HTTP</strong>
            (it must include the <code className="rounded bg-muted px-1">/mcp</code> suffix).
          </p>
        </div>
        <AddPrimitiveDialog serverName={serverName} onAdded={refresh} />
      </div>

      <div className="rounded-lg border p-4 space-y-2">
        <div className="flex items-center justify-between">
          <Label className="text-sm font-medium">Server Description</Label>
          {!editingDesc ? (
            <Button variant="ghost" size="sm" onClick={() => setEditingDesc(true)}>
              <Pencil className="mr-1 h-3 w-3" />
              Edit
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={savingDesc}
              onClick={async () => {
                setSavingDesc(true);
                try {
                  await api.updateDescription(serverName, localDesc);
                  setEditingDesc(false);
                  refresh();
                } finally {
                  setSavingDesc(false);
                }
              }}
            >
              <Check className="mr-1 h-3 w-3" />
              {savingDesc ? "Saving..." : "Save"}
            </Button>
          )}
        </div>
        {editingDesc ? (
          <>
            <p className="text-xs text-muted-foreground">
              Describe the server's purpose and capabilities. This is passed to LLMs as context.
            </p>
            <Textarea
              className="min-h-[120px]"
              placeholder="This MCP server provides tools for..."
              value={localDesc}
              onChange={(e) => setLocalDesc(e.target.value)}
            />
          </>
        ) : localDesc ? (
          <p className="text-sm text-muted-foreground whitespace-pre-wrap">{localDesc}</p>
        ) : (
          <p className="text-sm text-muted-foreground italic">
            No description yet. Click Edit to add one.
          </p>
        )}
      </div>

      {server.primitives.length === 0 ? (
        <div className="rounded-lg border border-dashed p-12 text-center text-muted-foreground">
          No primitives yet. Click "Add Primitive" to define tools, resources, or prompts.
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Details</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {server.primitives.map((p) => (
                <TableRow key={`${p.kind}-${p.name}`}>
                  <TableCell className="font-medium font-mono text-sm">
                    {p.name}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={kindColors[p.kind] ?? ""}>
                      {kindLabels[p.kind] ?? p.kind}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm max-w-[200px] truncate">
                    {p.description || "\u2014"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {"parameters" in p && p.parameters.length > 0 && (
                      <span>
                        {p.parameters.map((pr) => pr.name).join(", ")}
                      </span>
                    )}
                    {"uri" in p && <code className="rounded bg-muted px-1">{p.uri}</code>}
                    {"uri_template" in p && (
                      <code className="rounded bg-muted px-1">{p.uri_template}</code>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <TestPrimitiveDialog
                        serverName={serverName}
                        primitive={p}
                        disabled={server.status !== "running"}
                      />
                      <AddPrimitiveDialog
                        serverName={serverName}
                        onAdded={refresh}
                        existing={p}
                      />
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={deleting === p.name}
                        onClick={() => handleDeletePrimitive(p)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-lg border p-4">
          <PackageManager
            packages={localPackages}
            onChange={setLocalPackages}
          />
        </div>
        <div className="rounded-lg border p-4">
          <ServerEnvBindingsEditor
            value={localEnvBindings}
            onChange={setLocalEnvBindings}
            globalCatalog={server.global_env ?? []}
          />
        </div>
      </div>

      <div className="rounded-lg border p-4">
        <ImportsEditor imports={localImports} onChange={setLocalImports} />
      </div>

      {(configDirty || deployError) && (
        <div className="sticky bottom-4 flex items-center justify-between rounded-lg border bg-card p-4 shadow-lg">
          <div>
            {deployError ? (
              <p className="text-sm text-destructive">{deployError}</p>
            ) : (
              <p className="text-sm text-muted-foreground">
                You have unsaved configuration changes.
              </p>
            )}
          </div>
          <Button onClick={handleDeploy} disabled={deploying}>
            {deploying ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Deploying...
              </>
            ) : (
              <>
                <Rocket className="mr-2 h-4 w-4" />
                Deploy Changes
              </>
            )}
          </Button>
        </div>
      )}
        </TabsContent>

        <TabsContent value="runtime" className="mt-6 space-y-6">
          <div className="rounded-lg border p-4 space-y-3">
            <Label className="text-sm font-medium">Deployment</Label>
            <p className="text-xs text-muted-foreground">
              {replicaLimits?.docker_swarm_mode ?? server.docker_swarm_mode
                ? "Docker Swarm: Traefik spreads traffic across tasks."
                : "Stand-alone Docker: one container per server. The replica value is stored and used if you move to Swarm."}
            </p>
            {(replicaLimits?.docker_swarm_mode ?? server.docker_swarm_mode) && (
              <p className="text-xs text-muted-foreground border-l-2 border-primary/40 pl-3">
                <strong className="text-foreground">Why tasks show &quot;No such image&quot;:</strong> MCP
                server images are built on the host that runs the platform API. Other Swarm nodes need the
                image from a registry. Set the <strong>Docker image registry</strong> under{" "}
                <strong>Platform Settings</strong> so builds are tagged and pushed; ensure workers can
                pull that registry.
              </p>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="desired-replicas" className="text-xs">
                  Desired replicas
                </Label>
                <Input
                  id="desired-replicas"
                  type="number"
                  min={1}
                  max={replicaLimits?.max_mcp_server_replicas ?? 32}
                  className="w-28"
                  value={localReplicas}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "") setLocalReplicas(1);
                    else setLocalReplicas(Math.max(1, parseInt(v, 10) || 1));
                  }}
                />
              </div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={
                  savingReplicas || localReplicas === server.replicas_desired
                }
                onClick={async () => {
                  setSavingReplicas(true);
                  try {
                    await api.updateServerReplicas(serverName, localReplicas);
                    await refresh();
                  } finally {
                    setSavingReplicas(false);
                  }
                }}
              >
                {savingReplicas ? "Saving…" : "Apply"}
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              Running: <strong>{server.replicas_running}</strong> task
              {server.replicas_running === 1 ? "" : "s"} (desired{" "}
              <strong>{server.replicas_desired}</strong>)
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={redeploying}
                onClick={() => void handleRedeploy()}
              >
                {redeploying ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Rebuilding…
                  </>
                ) : (
                  <>
                    <Rocket className="mr-2 h-4 w-4" />
                    Rebuild &amp; redeploy
                  </>
                )}
              </Button>
              <p className="text-xs text-muted-foreground max-w-[28rem]">
                Regenerates <code className="text-[0.8rem]">server.py</code> from the saved spec and
                rebuilds the Docker image. Use this after a platform upgrade or if containers fail to
                start.
              </p>
            </div>
            {server.placement.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">Task placement</p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>State</TableHead>
                      <TableHead>Node</TableHead>
                      <TableHead>Task</TableHead>
                      <TableHead>Slot</TableHead>
                      <TableHead>Error</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {server.placement.map((t) => (
                      <TableRow key={t.task_id}>
                        <TableCell className="font-mono text-xs">{t.state}</TableCell>
                        <TableCell className="text-xs">
                          {t.node_name ?? (t.node_id || "—")}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{t.task_id.slice(0, 12)}</TableCell>
                        <TableCell className="text-xs">{t.slot ?? "—"}</TableCell>
                        <TableCell className="text-xs text-destructive max-w-[180px] truncate">
                          {t.error ?? "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>

          <div className="rounded-lg border p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label className="text-sm font-medium">Server container logs</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={logsLoading}
                onClick={() => void loadLogs()}
              >
                {logsLoading ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-3 w-3" />
                )}
                Refresh
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Last lines from the Docker container or Swarm service for this MCP server (stdout/stderr).
            </p>
            {logsError && (
              <p className="text-sm text-destructive">{logsError}</p>
            )}
            {logs !== null && (
              <pre className="max-h-96 overflow-auto rounded-md border bg-muted/50 p-3 text-xs font-mono whitespace-pre-wrap">
                {logs}
              </pre>
            )}
            {logs === null && !logsError && !logsLoading && (
              <p className="text-xs text-muted-foreground">
                Click Refresh to load logs from the host.
              </p>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
