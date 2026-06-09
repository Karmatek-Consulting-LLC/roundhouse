import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Asset, type AssetListResponse, type Primitive, type Server, type UsageSnapshot } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import { PrimitiveForm } from "@/components/primitive-form";
import { ImportsEditor } from "@/components/imports-editor";
import { PackageManager } from "@/components/package-manager";
import { AptPackageManager } from "@/components/apt-package-manager";
import {
  ServerEnvBindingsEditor,
  type ServerEnvBindings,
} from "@/components/server-env-bindings-editor";
import { ServerAuthPanel } from "@/components/server-auth-panel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { useTheme } from "@/hooks/use-theme";
import {
  ArrowLeft,
  Activity,
  Boxes,
  Copy,
  Download,
  FileCode,
  FileText,
  Files,
  Globe,
  KeyRound,
  Loader2,
  Package,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Settings,
  Trash2,
  Upload,
  Variable,
} from "lucide-react";

interface ServerEditProps {
  serverName: string;
}

/**
 * URL-driven selection. Selection paths kept short and human-readable so
 * deep links are usable:
 *   primitives/{name}   → a specific primitive
 *   imports             → imports + globals block
 *   packages            → PyPI packages (pip)
 *   apt-packages        → OS packages (apt) - Dockerfile context
 *   env                 → environment variables (no file context)
 *   auth                → scopes + tokens (no file context)
 */
type Selection =
  | { kind: "overview" }
  | { kind: "primitive"; name: string }
  | { kind: "primitive-new" }
  | { kind: "imports" }
  | { kind: "packages" }
  | { kind: "apt-packages" }
  | { kind: "env" }
  | { kind: "auth" }
  | { kind: "assets" }
  | { kind: "usage" }
  | { kind: "logs" }
  | { kind: "source" }
  | { kind: "remote" };

function parseSelection(path: string): Selection {
  // Empty path defaults to overview - that's the editor's home base.
  if (!path) return { kind: "overview" };
  if (path === "overview") return { kind: "overview" };
  if (path === "primitives:new") return { kind: "primitive-new" };
  if (path.startsWith("primitives/")) {
    return { kind: "primitive", name: decodeURIComponent(path.slice("primitives/".length)) };
  }
  if (path === "imports") return { kind: "imports" };
  if (path === "packages") return { kind: "packages" };
  if (path === "apt-packages") return { kind: "apt-packages" };
  if (path === "env") return { kind: "env" };
  if (path === "auth") return { kind: "auth" };
  if (path === "assets") return { kind: "assets" };
  if (path === "usage") return { kind: "usage" };
  if (path === "logs") return { kind: "logs" };
  if (path === "source") return { kind: "source" };
  if (path === "remote") return { kind: "remote" };
  return { kind: "overview" };
}

const kindLabels: Record<Primitive["kind"], { label: string; group: string }> = {
  tool: { label: "Tool", group: "Tools" },
  resource: { label: "Resource", group: "Resources" },
  resource_template: { label: "Resource", group: "Resource Templates" },
  prompt: { label: "Prompt", group: "Prompts" },
};

const kindDotColor: Record<Primitive["kind"], string> = {
  tool: "bg-blue-500",
  resource: "bg-purple-500",
  resource_template: "bg-indigo-500",
  prompt: "bg-amber-500",
};

const groupOrder = ["Tools", "Resources", "Resource Templates", "Prompts"] as const;

// ---------------- Right rail ----------------

interface RightRailProps {
  serverName: string;
  server: Server;
  selection: Selection;
  onSaved: () => void;
  /** Called after the server is deleted - parent should navigate home. */
  onDeleted: () => void;
  gotoPrimitive: (name: string) => void;
}

function RightRail({ serverName, server, selection, onSaved, onDeleted, gotoPrimitive }: RightRailProps) {
  if (selection.kind === "overview") {
    return (
      <OverviewRail
        serverName={serverName}
        server={server}
        onSaved={onSaved}
        onDeleted={onDeleted}
      />
    );
  }
  if (selection.kind === "logs") {
    return <LogsRail serverName={serverName} server={server} onMutated={onSaved} />;
  }
  if (selection.kind === "source") {
    return <SourceRail serverName={serverName} server={server} onSaved={onSaved} />;
  }
  if (selection.kind === "remote") {
    return <RemoteRail serverName={serverName} server={server} onSaved={onSaved} />;
  }
  if (selection.kind === "primitive-new") {
    return (
      <>
        <RailHeader>New primitive</RailHeader>
        <div>
          <PrimitiveForm
            serverName={serverName}
            layout="panel"
            onSaved={(name) => {
              onSaved();
              gotoPrimitive(name);
            }}
          />
        </div>
      </>
    );
  }

  if (selection.kind === "primitive") {
    const prim = (server.primitives ?? []).find((p) => p.name === selection.name);
    if (!prim) {
      return (
        <p className="text-muted-foreground italic">
          Primitive <code className="rounded bg-muted px-1">{selection.name}</code> not found.
        </p>
      );
    }
    return (
      <>
        <RailHeader>
          Editing: <span className="font-mono">{prim.name}</span>
        </RailHeader>
        <div>
          <PrimitiveForm
            key={prim.name}
            serverName={serverName}
            existing={prim}
            layout="panel"
            serverRunning={server.status === "running"}
            redeployPending={!!server.redeploy_required_at}
            readOnlySchema={!!prim.discovered}
            onSaved={onSaved}
          />
        </div>
      </>
    );
  }

  if (selection.kind === "imports") {
    return (
      <ImportsRail serverName={serverName} server={server} onSaved={onSaved} />
    );
  }
  if (selection.kind === "packages") {
    return (
      <PackagesRail serverName={serverName} server={server} onSaved={onSaved} />
    );
  }
  if (selection.kind === "apt-packages") {
    return (
      <AptPackagesRail serverName={serverName} server={server} onSaved={onSaved} />
    );
  }
  if (selection.kind === "env") {
    return (
      <EnvRail serverName={serverName} server={server} onSaved={onSaved} />
    );
  }
  if (selection.kind === "auth") {
    return (
      <>
        <RailHeader>Auth</RailHeader>
        <div>
          <ServerAuthPanel serverName={serverName} onMutated={onSaved} />
        </div>
      </>
    );
  }
  if (selection.kind === "assets") {
    return <AssetsRail serverName={serverName} server={server} onMutated={onSaved} />;
  }
  if (selection.kind === "usage") {
    return <UsageRail serverName={serverName} server={server} />;
  }

  return (
    <p className="text-muted-foreground italic">
      Pick a primitive or configuration item on the left.
    </p>
  );
}

// ---- Per-section rails. Each loads initial value from `server`, manages
// local state, calls its dedicated API endpoint on Save, and refreshes the
// parent so the center file preview repaints with the new content.

interface RailProps {
  serverName: string;
  server: Server;
  onSaved: () => void;
}

function SaveBar({
  dirty,
  saving,
  onSave,
  onReset,
  error,
}: {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onReset: () => void;
  error: string | null;
}) {
  return (
    <div className="border-t pt-3 pb-1 mt-3 flex items-center gap-2">
      {error && <p className="text-xs text-destructive flex-1">{error}</p>}
      {!error && <span className="text-xs text-muted-foreground flex-1">
        {dirty ? "Unsaved changes" : "Up to date"}
      </span>}
      <Button variant="ghost" size="sm" onClick={onReset} disabled={!dirty || saving}>
        Reset
      </Button>
      <Button size="sm" onClick={onSave} disabled={!dirty || saving}>
        {saving ? "Saving..." : "Save"}
      </Button>
    </div>
  );
}

function useDirty<T>(initial: T) {
  const [value, setValue] = useState(initial);
  const [savedSnapshot, setSavedSnapshot] = useState(initial);
  const dirty = JSON.stringify(value) !== JSON.stringify(savedSnapshot);
  return {
    value,
    setValue,
    dirty,
    reset: () => setValue(savedSnapshot),
    markSaved: (v: T) => setSavedSnapshot(v),
  };
}

function ImportsRail({ serverName, server, onSaved }: RailProps) {
  const { value, setValue, dirty, reset, markSaved } = useDirty(server.imports ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setSaving(true);
    try {
      // No dedicated /imports endpoint yet - use deployConfig with current
      // other values so a focused imports save doesn't blow away packages/env.
      await api.deployConfig(serverName, value, server.pip_packages ?? [], server.apt_packages ?? [], {
        env_global_imports: server.env_global_imports ?? [],
        env_vars: server.env_vars ?? [],
      });
      markSaved(value);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save imports");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col">
      <RailHeader>Imports &amp; globals</RailHeader>
      <div>
        <ImportsEditor imports={value} onChange={setValue} />
      </div>
      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={reset} error={error} />
    </div>
  );
}

function PackagesRail({ serverName, server, onSaved }: RailProps) {
  const { value, setValue, dirty, reset, markSaved } = useDirty(server.pip_packages ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setSaving(true);
    try {
      await api.updatePipPackages(serverName, value);
      markSaved(value);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save packages");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col">
      <RailHeader>PyPI packages</RailHeader>
      <div>
        <PackageManager packages={value} onChange={setValue} />
      </div>
      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={reset} error={error} />
    </div>
  );
}

function AptPackagesRail({ serverName, server, onSaved }: RailProps) {
  const { value, setValue, dirty, reset, markSaved } = useDirty(server.apt_packages ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setSaving(true);
    try {
      await api.updateAptPackages(serverName, value);
      markSaved(value);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save apt packages");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col">
      <RailHeader>OS packages (apt)</RailHeader>
      <div>
        <AptPackageManager packages={value} onChange={setValue} />
      </div>
      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={reset} error={error} />
    </div>
  );
}

function EnvRail({ serverName, server, onSaved }: RailProps) {
  const initial: ServerEnvBindings = {
    env_global_imports: server.env_global_imports ?? [],
    env_vars: server.env_vars ?? [],
  };
  const { value, setValue, dirty, reset, markSaved } = useDirty(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setSaving(true);
    try {
      const filteredLocals = value.env_vars.filter((v) => v.name.trim());
      await api.updateEnvVars(serverName, {
        env_global_imports: value.env_global_imports,
        env_vars: filteredLocals,
      });
      markSaved({ env_global_imports: value.env_global_imports, env_vars: filteredLocals });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save env");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col">
      <RailHeader>Environment variables</RailHeader>
      <div>
        <ServerEnvBindingsEditor
          value={value}
          onChange={setValue}
          globalCatalog={server.global_env ?? []}
        />
      </div>
      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={reset} error={error} />
    </div>
  );
}

// Sticky banner shown whenever there are saved-but-not-deployed changes.
function RedeployBanner({
  serverName,
  server,
  onRedeployed,
}: {
  serverName: string;
  server: Server;
  onRedeployed: () => void;
}) {
  const [redeploying, setRedeploying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!server.redeploy_required_at) return null;

  async function redeploy() {
    setError(null);
    setRedeploying(true);
    try {
      await api.redeployServer(serverName);
      onRedeployed();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Redeploy failed");
    } finally {
      setRedeploying(false);
    }
  }

  return (
    <div className="border-b border-amber-500/40 bg-amber-500/10 px-4 sm:px-6 lg:px-8 py-2 flex items-center gap-3 text-sm text-amber-950 dark:text-amber-100">
      <Rocket className="h-4 w-4 flex-shrink-0" />
      <span className="flex-1">
        <strong>Changes pending.</strong> Spec is saved; redeploy to apply.
        {error && <span className="ml-2 text-destructive">— {error}</span>}
      </span>
      <Button size="sm" onClick={redeploy} disabled={redeploying}>
        {redeploying ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Rocket className="mr-1 h-3 w-3" />}
        {redeploying ? "Redeploying..." : "Redeploy"}
      </Button>
    </div>
  );
}

function RailHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
    </div>
  );
}

// ---------------- Overview rail ----------------

interface OverviewRailProps {
  serverName: string;
  server: Server;
  onSaved: () => void;
  onDeleted: () => void;
}

function OverviewRail({ serverName, server, onSaved, onDeleted }: OverviewRailProps) {
  const [description, setDescription] = useState(server.description ?? "");
  const [savedDescription, setSavedDescription] = useState(server.description ?? "");
  const [replicas, setReplicas] = useState<number>(server.replicas_desired ?? 1);
  const [savedReplicas, setSavedReplicas] = useState<number>(server.replicas_desired ?? 1);
  // Empty string = no limit (Docker default). Stored as string in state so
  // the input can be cleared without coercing to 0.
  const [cpuLimit, setCpuLimit] = useState<string>(server.cpu_limit != null ? String(server.cpu_limit) : "");
  const [memoryLimit, setMemoryLimit] = useState<string>(server.memory_limit_mb != null ? String(server.memory_limit_mb) : "");
  const [savedCpuLimit, setSavedCpuLimit] = useState<string>(cpuLimit);
  const [savedMemoryLimit, setSavedMemoryLimit] = useState<string>(memoryLimit);
  const [replicaLimits, setReplicaLimits] = useState<{
    max_mcp_server_replicas: number;
    docker_swarm_mode: boolean;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [lifecycle, setLifecycle] = useState<"" | "start" | "stop" | "redeploy" | "update-git" | "delete">("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getServerReplicaLimits().then(setReplicaLimits).catch(() => setReplicaLimits(null));
  }, []);

  const descDirty = description !== savedDescription;
  const replicasDirty = replicas !== savedReplicas;
  const resourcesDirty = cpuLimit !== savedCpuLimit || memoryLimit !== savedMemoryLimit;
  const dirty = descDirty || replicasDirty || resourcesDirty;

  async function save() {
    setError(null);
    setSaving(true);
    try {
      const tasks: Promise<unknown>[] = [];
      if (descDirty) tasks.push(api.updateDescription(serverName, description));
      if (replicasDirty) tasks.push(api.updateServerReplicas(serverName, replicas));
      if (resourcesDirty) {
        const cpu = parseFloat(cpuLimit);
        const mem = parseInt(memoryLimit, 10);
        tasks.push(api.updateServerResources(serverName, {
          cpu_limit: !Number.isNaN(cpu) && cpu > 0 ? cpu : null,
          memory_limit_mb: !Number.isNaN(mem) && mem > 0 ? mem : null,
        }));
      }
      await Promise.all(tasks);
      setSavedDescription(description);
      setSavedReplicas(replicas);
      setSavedCpuLimit(cpuLimit);
      setSavedMemoryLimit(memoryLimit);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    setDescription(savedDescription);
    setReplicas(savedReplicas);
    setCpuLimit(savedCpuLimit);
    setMemoryLimit(savedMemoryLimit);
    setError(null);
  }

  async function exportSpec() {
    setError(null);
    try {
      const dump = await api.exportServer(serverName);
      const blob = new Blob([JSON.stringify(dump, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${serverName}.spec.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  }

  async function lifecycleAction(action: "start" | "stop" | "redeploy" | "update-git" | "delete") {
    setError(null);
    if (action === "delete" && !confirm(`Delete server "${serverName}"? This removes the container/service and the stored spec.`)) {
      return;
    }
    setLifecycle(action);
    try {
      if (action === "start") await api.startServer(serverName);
      else if (action === "stop") await api.stopServer(serverName);
      else if (action === "redeploy") await api.redeployServer(serverName);
      else if (action === "update-git") await api.updateFromGit(serverName);
      else {
        await api.deleteServer(serverName);
        onDeleted();
        return;
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to ${action}`);
    } finally {
      setLifecycle("");
    }
  }

  const maxReplicas = replicaLimits?.max_mcp_server_replicas ?? 32;
  const showReplicasHelp = replicaLimits?.docker_swarm_mode === false;
  const statusBad = server.status === "not_deployed" || server.status === "unknown";

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      <RailHeader>Overview</RailHeader>

      {statusBad && (
        <div
          className={
            "flex items-center gap-3 rounded-lg border px-4 py-3 text-sm " +
            (server.status === "unknown"
              ? "border-destructive/50 bg-destructive/10 text-destructive"
              : "border-amber-500/40 bg-amber-500/10 text-amber-950 dark:text-amber-100")
          }
        >
          <span className="flex-1">
            {server.status === "unknown" ? (
              <><strong>Docker status unavailable.</strong> Platform could not read this server from Docker.</>
            ) : (
              <><strong>Not deployed.</strong> Set any required environment variables, then deploy to build and start the server.</>
            )}
          </span>
          {server.status === "not_deployed" && (
            <Button size="sm" disabled={!!lifecycle} onClick={() => lifecycleAction("redeploy")}>
              {lifecycle === "redeploy" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Rocket className="mr-1 h-3 w-3" />}
              Deploy
            </Button>
          )}
        </div>
      )}

      <div className="grid gap-2">
        <Label>Description</Label>
        <p className="text-xs text-muted-foreground">
          Passed to LLMs as context for the server. Describe its purpose and capabilities.
        </p>
        <Textarea
          className="min-h-[100px]"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="grid gap-2 max-w-xs">
        <Label>Replicas</Label>
        <p className="text-xs text-muted-foreground">
          Desired number of tasks. Only meaningful in Docker Swarm; standalone Docker always runs 1.
        </p>
        <Input
          type="number"
          min={1}
          max={maxReplicas}
          value={replicas}
          onChange={(e) => setReplicas(Math.max(1, Math.min(maxReplicas, Number(e.target.value) || 1)))}
        />
        {showReplicasHelp && (
          <p className="text-xs italic text-muted-foreground">
            Stored but not applied — running in stand-alone Docker.
          </p>
        )}
      </div>

      <div className="grid gap-2">
        <Label>Resource limits</Label>
        <p className="text-xs text-muted-foreground">
          Caps applied to the container at deploy time. Blank = no limit (Docker default).
          Changes take effect on the next <strong>Redeploy</strong>.
        </p>
        <div className="grid grid-cols-2 gap-3 max-w-md">
          <div className="grid gap-1">
            <Label className="text-xs text-muted-foreground">CPUs</Label>
            <Input
              type="number"
              inputMode="decimal"
              step="0.1"
              min={0}
              placeholder="—"
              value={cpuLimit}
              onChange={(e) => setCpuLimit(e.target.value)}
            />
          </div>
          <div className="grid gap-1">
            <Label className="text-xs text-muted-foreground">Memory (MB)</Label>
            <Input
              type="number"
              inputMode="numeric"
              min={0}
              placeholder="—"
              value={memoryLimit}
              onChange={(e) => setMemoryLimit(e.target.value)}
            />
          </div>
        </div>
      </div>

      <div className="border-t pt-4 flex flex-wrap items-center gap-2">
        <Label className="text-sm font-medium mr-2">Server actions</Label>
        <Button
          size="sm"
          variant="outline"
          disabled={!!lifecycle || server.status === "running"}
          onClick={() => lifecycleAction("start")}
        >
          {lifecycle === "start" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Play className="mr-1 h-3 w-3" />}
          Start
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!!lifecycle || server.status !== "running"}
          onClick={() => lifecycleAction("stop")}
        >
          {lifecycle === "stop" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Pause className="mr-1 h-3 w-3" />}
          Stop
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!!lifecycle}
          onClick={() => lifecycleAction("redeploy")}
        >
          {lifecycle === "redeploy" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Rocket className="mr-1 h-3 w-3" />}
          Redeploy
        </Button>
        {server.git_url && (
          <Button
            size="sm"
            variant="outline"
            disabled={!!lifecycle}
            title={`Re-clone ${server.git_url}${server.git_ref ? ` @ ${server.git_ref}` : ""} and merge into the spec`}
            onClick={() => lifecycleAction("update-git")}
          >
            {lifecycle === "update-git" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />}
            Update from Git
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={!!lifecycle}
          onClick={() => exportSpec()}
        >
          <Download className="mr-1 h-3 w-3" />
          Export spec
        </Button>
        <Button
          size="sm"
          variant="destructive"
          disabled={!!lifecycle}
          onClick={() => lifecycleAction("delete")}
        >
          {lifecycle === "delete" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Trash2 className="mr-1 h-3 w-3" />}
          Delete server
        </Button>
      </div>

      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={reset} error={error} />
    </div>
  );
}

// ---------------- Logs rail ----------------

type StreamState = "connecting" | "open" | "closed" | "error";

// Cap the buffer so a long-running tail doesn't eat memory. Plenty for human
// inspection; the underlying Docker stream is always queryable from scratch.
const MAX_LOG_LINES = 5000;

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
type LogLevel = (typeof LOG_LEVELS)[number];

function LogsRail({
  serverName,
  server,
  onMutated,
}: {
  serverName: string;
  server: Server;
  onMutated: () => void;
}) {
  const [lines, setLines] = useState<string[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [tail, setTail] = useState(400);
  const [follow, setFollow] = useState(true);
  const [paused, setPaused] = useState(false);
  const [savingLevel, setSavingLevel] = useState(false);
  const [levelError, setLevelError] = useState<string | null>(null);

  // Dropdown only applies to spec-based servers: the platform middleware reads
  // LOG_LEVEL and configures stdlib logging. Code-mode servers own their own
  // logging surface, so the dropdown would be a hollow promise there.
  const showLogLevel = server.mode === "structured";
  const currentLevel: LogLevel = (() => {
    const v = (server.env_vars ?? []).find((ev) => ev.name === "LOG_LEVEL")?.value;
    const upper = (v || "").trim().toUpperCase();
    return (LOG_LEVELS as readonly string[]).includes(upper) ? (upper as LogLevel) : "INFO";
  })();

  async function changeLogLevel(next: LogLevel) {
    if (next === currentLevel) return;
    setLevelError(null);
    setSavingLevel(true);
    try {
      const existing = server.env_vars ?? [];
      const has = existing.some((ev) => ev.name === "LOG_LEVEL");
      const env_vars = has
        ? existing.map((ev) => (ev.name === "LOG_LEVEL" ? { ...ev, value: next } : ev))
        : [...existing, { name: "LOG_LEVEL", value: next, secret: false }];
      await api.updateEnvVars(serverName, {
        env_global_imports: server.env_global_imports ?? [],
        env_vars,
      });
      onMutated();
    } catch (e) {
      setLevelError(e instanceof Error ? e.message : "Failed to set log level");
    } finally {
      setSavingLevel(false);
    }
  }

  // Used to force-reconnect on demand (button click) or when tail changes.
  const [streamKey, setStreamKey] = useState(0);

  const preRef = useRef<HTMLPreElement | null>(null);
  // When paused, we still receive events but stash them here without rendering,
  // so the buffer doesn't grow unbounded and we keep a single "resume" cliff.
  const pausedBufferRef = useRef<string[]>([]);
  // Mirror `paused` into a ref so the EventSource handler (captured in a
  // closure for the lifetime of the stream) always reads the current value.
  const pausedRef = useRef(paused);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    setLines([]);
    setError(null);
    setState("connecting");

    const token = localStorage.getItem("token") ?? "";
    const url = `/api/servers/${encodeURIComponent(serverName)}/logs/stream?tail=${tail}&token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);

    es.addEventListener("open", () => {
      // Custom "open" event from the backend - confirms the stream is live
      // even before Docker pushes the first chunk.
      setState("open");
    });

    es.onopen = () => {
      // Browser-level connection open. The backend sends an "event: open"
      // SSE marker too, which the listener above picks up.
      setState((s) => (s === "error" ? "open" : s));
    };

    es.onmessage = (evt) => {
      const line = evt.data;
      if (pausedRef.current) {
        pausedBufferRef.current.push(line);
        if (pausedBufferRef.current.length > MAX_LOG_LINES) {
          pausedBufferRef.current.splice(0, pausedBufferRef.current.length - MAX_LOG_LINES);
        }
        return;
      }
      setLines((prev) => {
        const next = prev.length >= MAX_LOG_LINES ? prev.slice(prev.length - MAX_LOG_LINES + 1) : prev.slice();
        next.push(line);
        return next;
      });
    };

    es.addEventListener("error", (evt) => {
      // Backend may emit a structured error event; browser also fires onerror
      // on transport problems. Treat them the same - show the message we have.
      const data = (evt as MessageEvent).data;
      if (typeof data === "string" && data) {
        setError(data);
      }
    });

    es.onerror = () => {
      // EventSource auto-reconnects unless we close. We close so the user
      // sees an explicit "Reconnect" affordance instead of silent retries.
      setState("error");
      es.close();
    };

    return () => {
      es.close();
      setState("closed");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverName, tail, streamKey]);

  // Auto-scroll to bottom on new lines when `follow` is on.
  useEffect(() => {
    if (!follow) return;
    const pre = preRef.current;
    if (!pre) return;
    pre.scrollTop = pre.scrollHeight;
  }, [lines, follow]);

  function resume() {
    setPaused(false);
    // Flush stashed lines into the rendered buffer.
    if (pausedBufferRef.current.length > 0) {
      setLines((prev) => {
        const merged = [...prev, ...pausedBufferRef.current];
        return merged.length > MAX_LOG_LINES ? merged.slice(merged.length - MAX_LOG_LINES) : merged;
      });
      pausedBufferRef.current = [];
    }
  }

  function reconnect() {
    pausedBufferRef.current = [];
    setStreamKey((k) => k + 1);
  }

  const statusBadge = (() => {
    if (state === "open" && !paused) return <span className="text-emerald-600 dark:text-emerald-400">● Streaming</span>;
    if (state === "open" && paused) return <span className="text-amber-600 dark:text-amber-400">⏸ Paused</span>;
    if (state === "connecting") return <span className="text-muted-foreground">Connecting…</span>;
    if (state === "error") return <span className="text-destructive">Disconnected</span>;
    return <span className="text-muted-foreground">Closed</span>;
  })();

  return (
    <div className="flex flex-col gap-3 max-w-5xl">
      <RailHeader>Logs</RailHeader>
      <div className="flex items-center gap-2 text-xs flex-wrap">
        <Label className="text-xs">Tail</Label>
        <Input
          type="number"
          min={0}
          max={5000}
          value={tail}
          onChange={(e) => setTail(Math.max(0, Math.min(5000, Number(e.target.value) || 0)))}
          className="w-24 h-8"
        />
        {paused ? (
          <Button size="sm" variant="outline" onClick={resume}>
            <Play className="mr-1 h-3 w-3" /> Resume
            {pausedBufferRef.current.length > 0 && (
              <span className="ml-1 text-muted-foreground">
                (+{pausedBufferRef.current.length})
              </span>
            )}
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setPaused(true)}>
            <Pause className="mr-1 h-3 w-3" /> Pause
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => setLines([])}>
          <Trash2 className="mr-1 h-3 w-3" /> Clear
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={reconnect}
          disabled={state === "connecting"}
        >
          <RefreshCw className="mr-1 h-3 w-3" /> Reconnect
        </Button>
        <label className="flex items-center gap-1 text-xs ml-1 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
          />
          Follow
        </label>
        {showLogLevel && (
          <div className="flex items-center gap-1.5 ml-1">
            <Label className="text-xs">Level</Label>
            <Select
              value={currentLevel}
              onValueChange={(v) => changeLogLevel(v as LogLevel)}
              disabled={savingLevel}
            >
              <SelectTrigger className="h-8 w-[120px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LOG_LEVELS.map((lvl) => (
                  <SelectItem key={lvl} value={lvl} className="text-xs font-mono">
                    {lvl}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <span className="ml-auto text-xs">{statusBadge}</span>
        {showLogLevel && levelError && (
          <span className="w-full text-destructive">{levelError}</span>
        )}
        {showLogLevel && !levelError && (
          <span className="w-full text-muted-foreground italic">
            Log level is read from the <code className="font-mono">LOG_LEVEL</code> env var; changes save instantly and take effect on the next redeploy.
          </span>
        )}
        {server.status !== "running" && (
          <span className="w-full text-muted-foreground italic">
            Server is {server.status} — logs may be empty or stale.
          </span>
        )}
      </div>
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}
      <pre
        ref={preRef}
        className="bg-muted/30 border rounded-md p-3 text-xs font-mono whitespace-pre-wrap break-all min-h-[400px] max-h-[70vh] overflow-y-auto"
      >
        {lines.length === 0
          ? state === "connecting"
            ? "(connecting…)"
            : "(no log lines yet)"
          : lines.join("\n")}
      </pre>
    </div>
  );
}

// ---------------- Assets rail ----------------

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function AssetsRail({
  serverName,
  server,
  onMutated,
}: {
  serverName: string;
  server: Server;
  onMutated: () => void;
}) {
  const [data, setData] = useState<AssetListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [copiedName, setCopiedName] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const resp = await api.listAssets(serverName);
      setData(resp);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load assets");
    } finally {
      setLoading(false);
    }
  }, [serverName]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(true);
    setError(null);
    try {
      for (const f of list) {
        await api.uploadAsset(serverName, f);
      }
      await refresh();
      onMutated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function remove(name: string) {
    if (!confirm(`Delete asset "${name}"?`)) return;
    setError(null);
    try {
      await api.deleteAsset(serverName, name);
      await refresh();
      onMutated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  async function copyPath(name: string) {
    try {
      await navigator.clipboard.writeText(`/app/assets/${name}`);
      setCopiedName(name);
      setTimeout(() => setCopiedName((c) => (c === name ? null : c)), 1500);
    } catch {
      setError("Couldn't copy to clipboard");
    }
  }

  async function download(name: string) {
    setError(null);
    try {
      // Authenticated fetch -> blob URL trick. A plain <a download> can't
      // carry the bearer token in localStorage, so the server would 401.
      const blob = await api.downloadAsset(serverName, name);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    }
  }

  const assets = data?.assets ?? [];
  const totalSize = data?.total_size ?? 0;
  const maxTotal = data?.max_total_bytes ?? 100 * 1024 * 1024;
  const maxFile = data?.max_file_bytes ?? 10 * 1024 * 1024;
  const fillPct = Math.min(100, Math.round((totalSize / maxTotal) * 100));

  return (
    <div className="space-y-4 max-w-3xl">
      <RailHeader>Assets</RailHeader>
      <p className="text-xs text-muted-foreground">
        Files baked into the server's container image at <code className="rounded bg-muted px-1">/app/assets/</code>.
        Reference them from your tool code as <code className="rounded bg-muted px-1">(ASSETS_DIR / "filename").read_text()</code>{" "}
        or with the absolute path. Uploads take effect on next <strong>Redeploy</strong>.
      </p>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files);
        }}
        className={`rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragOver ? "border-primary bg-primary/5" : "border-border bg-muted/20"
        }`}
      >
        <Upload className="mx-auto mb-2 h-6 w-6 text-muted-foreground" />
        <p className="text-sm">
          Drop files here, or{" "}
          <button
            type="button"
            className="underline underline-offset-2 hover:text-primary"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            choose files
          </button>
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Max {formatBytes(maxFile)} per file, {formatBytes(maxTotal)} per server.
        </p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) uploadFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full transition-all ${fillPct > 90 ? "bg-amber-500" : "bg-primary"}`}
            style={{ width: `${fillPct}%` }}
          />
        </div>
        <span className="tabular-nums">
          {formatBytes(totalSize)} / {formatBytes(maxTotal)}
        </span>
      </div>

      {server.redeploy_required_at && assets.length > 0 && (
        <p className="text-xs italic text-amber-700 dark:text-amber-300">
          Asset changes pending — redeploy to bake them into the image.
        </p>
      )}

      {loading ? (
        <p className="text-xs italic text-muted-foreground">Loading…</p>
      ) : assets.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">No assets uploaded yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-1 pr-3 font-medium">Filename</th>
                <th className="py-1 pr-3 font-medium text-right">Size</th>
                <th className="py-1 pr-3 font-medium">Path</th>
                <th className="py-1 font-medium" />
              </tr>
            </thead>
            <tbody>
              {assets.map((a: Asset) => (
                <tr key={a.name} className="border-b last:border-0">
                  <td className="py-1 pr-3 font-mono">{a.name}</td>
                  <td className="py-1 pr-3 text-right tabular-nums">{formatBytes(a.size)}</td>
                  <td className="py-1 pr-3 font-mono text-muted-foreground">
                    /app/assets/{a.name}
                  </td>
                  <td className="py-1 text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Copy absolute path"
                      onClick={() => copyPath(a.name)}
                    >
                      <Copy className={`h-4 w-4 ${copiedName === a.name ? "text-emerald-600" : "text-muted-foreground"}`} />
                    </Button>
                    <Button variant="ghost" size="icon" title="Download" onClick={() => download(a.name)}>
                      <Download className="h-4 w-4 text-muted-foreground" />
                    </Button>
                    <Button variant="ghost" size="icon" title="Delete" onClick={() => remove(a.name)}>
                      <Trash2 className="h-4 w-4 text-muted-foreground" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------- Usage rail ----------------

function formatRelative(epochSec: number, nowSec: number): string {
  if (!epochSec) return "never";
  const delta = Math.max(0, Math.floor(nowSec - epochSec));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function formatMs(v: number | null): string {
  if (v == null) return "—";
  if (v < 1) return v.toFixed(2) + " ms";
  if (v < 100) return v.toFixed(1) + " ms";
  return Math.round(v) + " ms";
}

function UsageRail({ serverName, server }: { serverName: string; server: Server }) {
  const [snap, setSnap] = useState<UsageSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getServerUsage(serverName);
      setSnap(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch usage");
    } finally {
      setLoading(false);
    }
  }, [serverName]);

  useEffect(() => {
    refresh();
    // 5s poll while the rail is visible. The endpoint is cheap (one HTTP hop
    // inside the Docker network) so this is a fine resolution for a human
    // staring at the page; collapse to on-demand if it becomes an issue.
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <div className="space-y-3">
      <RailHeader>Usage</RailHeader>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={refresh} disabled={loading}>
          <RefreshCw className="mr-1 h-3 w-3" /> Refresh
        </Button>
        {snap && snap.available && (
          <span className="text-xs text-muted-foreground">
            Since process start ({formatRelative(snap.started_ts, snap.now_ts)})
          </span>
        )}
        {server.status !== "running" && (
          <span className="text-xs italic text-muted-foreground ml-auto">
            Server is {server.status} — metrics unavailable.
          </span>
        )}
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {snap && snap.available === false && !error && (
        <p className="text-xs italic text-muted-foreground">
          No metrics yet — the server may need a redeploy to pick up the metrics endpoint,
          or no requests have been served since startup.
        </p>
      )}

      {snap && snap.available && (
        <>
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">
              Per primitive
            </div>
            {snap.primitives.length === 0 ? (
              <p className="text-xs italic text-muted-foreground">No calls yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="py-1 pr-3 font-medium">Name</th>
                      <th className="py-1 pr-3 font-medium">Kind</th>
                      <th className="py-1 pr-3 font-medium text-right">Calls</th>
                      <th className="py-1 pr-3 font-medium text-right">Errors</th>
                      <th className="py-1 pr-3 font-medium text-right">p50</th>
                      <th className="py-1 pr-3 font-medium text-right">p95</th>
                      <th className="py-1 pr-3 font-medium text-right">p99</th>
                      <th className="py-1 font-medium">Last call</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snap.primitives.map((p) => (
                      <tr key={p.kind + ":" + p.name} className="border-b last:border-0">
                        <td className="py-1 pr-3 font-mono">{p.name}</td>
                        <td className="py-1 pr-3 text-muted-foreground">{p.kind}</td>
                        <td className="py-1 pr-3 text-right tabular-nums">{p.calls}</td>
                        <td className={`py-1 pr-3 text-right tabular-nums ${p.errors > 0 ? "text-destructive" : ""}`}>
                          {p.errors}
                        </td>
                        <td className="py-1 pr-3 text-right tabular-nums">{formatMs(p.p50_ms)}</td>
                        <td className="py-1 pr-3 text-right tabular-nums">{formatMs(p.p95_ms)}</td>
                        <td className="py-1 pr-3 text-right tabular-nums">{formatMs(p.p99_ms)}</td>
                        <td className="py-1 text-muted-foreground">{formatRelative(p.last_call_ts, snap.now_ts)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1 mt-3">
              Per token / primitive
            </div>
            {snap.tokens.length === 0 ? (
              <p className="text-xs italic text-muted-foreground">No calls yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="py-1 pr-3 font-medium">Token</th>
                      <th className="py-1 pr-3 font-medium">Primitive</th>
                      <th className="py-1 pr-3 font-medium text-right">Calls</th>
                      <th className="py-1 font-medium">Last call</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snap.tokens.map((t, i) => (
                      <tr key={i} className="border-b last:border-0">
                        <td className="py-1 pr-3 font-mono">{t.client_id ?? <span className="italic text-muted-foreground">(unauthenticated)</span>}</td>
                        <td className="py-1 pr-3 font-mono">{t.name}</td>
                        <td className="py-1 pr-3 text-right tabular-nums">{t.calls}</td>
                        <td className="py-1 text-muted-foreground">{formatRelative(t.last_call_ts, snap.now_ts)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------- Source rail (code mode) ----------------

function SourceRail({ serverName, server, onSaved }: { serverName: string; server: Server; onSaved: () => void }) {
  const { value, setValue, dirty, reset, markSaved } = useDirty(server.source ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setSaving(true);
    try {
      await api.updateSource(serverName, value);
      markSaved(value);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save source");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <RailHeader>server.py (code-first)</RailHeader>
      <p className="text-xs text-muted-foreground">
        The entire generated <code className="rounded bg-muted px-1">server.py</code>. You own this
        file - imports, FastMCP construction, primitives, everything.
      </p>
      <SourceEditor value={value} onChange={setValue} />
      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={reset} error={error} />
    </div>
  );
}

function SourceEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { resolvedTheme } = useTheme();
  return (
    <div className="rounded-md border overflow-hidden">
      <CodeMirror
        value={value}
        onChange={onChange}
        theme={resolvedTheme}
        extensions={[python()]}
        minHeight="500px"
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLine: true,
          indentOnInput: true,
          bracketMatching: true,
          autocompletion: true,
        }}
      />
    </div>
  );
}

// ---------------- Rediscover + Remote rail ----------------

/** Re-introspects a proxied server's toolset and refreshes on success. */
function RediscoverButton({ serverName, onDone }: { serverName: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="px-3 mb-3">
      <Button
        size="sm"
        variant="outline"
        className="w-full justify-start"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            await api.rediscoverServer(serverName);
            onDone();
          } catch (e) {
            setErr(e instanceof Error ? e.message : "Rediscovery failed");
          } finally {
            setBusy(false);
          }
        }}
      >
        <RefreshCw className={`mr-1 h-3 w-3 ${busy ? "animate-spin" : ""}`} />
        {busy ? "Rediscovering…" : "Rediscover"}
      </Button>
      {err && <p className="mt-1 text-[11px] text-destructive">{err}</p>}
    </div>
  );
}

interface RemoteRailProps {
  serverName: string;
  server: Server;
  onSaved: () => void;
}

/** Read-only view of a remote server's upstream config + access policy, with a
 * Rediscover action. Secret header values are never shown. */
function RemoteRail({ serverName, server, onSaved }: RemoteRailProps) {
  const headers = server.remote_headers ?? [];
  return (
    <>
      <RailHeader>Upstream</RailHeader>
      <div className="max-w-2xl space-y-5">
        <div className="grid gap-1">
          <Label>MCP URL</Label>
          <code className="break-all rounded bg-muted px-2 py-1 text-xs font-mono">
            {server.remote_url ?? "—"}
          </code>
        </div>

        <div className="grid gap-1">
          <Label>Outbound headers</Label>
          {headers.length === 0 ? (
            <p className="text-xs text-muted-foreground">No outbound headers configured.</p>
          ) : (
            <ul className="space-y-1 text-xs font-mono">
              {headers.map((h) => (
                <li key={h.env} className="rounded bg-muted px-2 py-1">
                  {h.header}{" "}
                  <span className="text-muted-foreground">{`→ $${h.env} (encrypted)`}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">
            Secret values are stored encrypted and never displayed. To rotate a credential, update
            its env var under <span className="font-medium">Env vars</span>, then redeploy. The
            caller's own token is never forwarded to the upstream.
          </p>
        </div>

        <div className="grid gap-1">
          <Label>Access policy</Label>
          <p className="text-xs text-muted-foreground">
            {server.deny_unlisted
              ? "Default-deny — discovered tools are locked until you assign a scope (create scopes under Auth, then assign per tool)."
              : "Default-allow — tools with no assigned scope are callable by any valid token."}
          </p>
        </div>

        <div className="grid gap-1">
          <Label>Toolset</Label>
          <p className="text-xs text-muted-foreground">
            {(server.primitives ?? []).length} discovered primitive(s). Rediscover after the
            upstream's tools change.
          </p>
          <div className="-mx-3">
            <RediscoverButton serverName={serverName} onDone={onSaved} />
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------- Left nav ----------------

interface LeftNavProps {
  server: Server;
  selection: Selection;
  onSelect: (path: string) => void;
  /** Refresh after a Rediscover so the new toolset renders. */
  onMutated: () => void;
}

function LeftNav({ server, selection, onSelect, onMutated }: LeftNavProps) {
  const isCodeMode = server.mode === "code";
  const isRemote = server.mode === "remote";
  // Proxied = the platform fronts a server it didn't author (code or remote):
  // primitives are DISCOVERED (read-only schema, scope-only edits) rather than
  // authored in the UI.
  const isProxied = isCodeMode || isRemote;

  // Group primitives by their display group.
  const groups = useMemo(() => {
    const out = new Map<string, Primitive[]>();
    for (const g of groupOrder) out.set(g, []);
    for (const p of server.primitives ?? []) {
      const g = kindLabels[p.kind]?.group;
      if (g) out.get(g)!.push(p);
    }
    for (const arr of out.values()) {
      arr.sort((a, b) => a.name.localeCompare(b.name));
    }
    return out;
  }, [server.primitives]);

  // Primitive groups render identically for authored (structured) and
  // discovered (proxied) servers; archived (vanished-upstream) entries are
  // shown struck-through so an operator can see what disappeared.
  const groupsNode = groupOrder.map((group) => {
    const items = groups.get(group) ?? [];
    if (items.length === 0) return null;
    return (
      <div key={group} className="mb-3 border-t pt-3">
        <NavHeading>{group}</NavHeading>
        {items.map((p) => (
          <NavItem
            key={`${p.kind}:${p.name}`}
            active={selection.kind === "primitive" && selection.name === p.name}
            onClick={() => onSelect(`primitives/${encodeURIComponent(p.name)}`)}
          >
            <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${kindDotColor[p.kind]}`} />
            <span
              title={p.name}
              className={`min-w-0 flex-1 truncate font-mono text-xs ${p.archived ? "text-muted-foreground line-through" : ""}`}
            >
              {p.name}
            </span>
            {p.archived && (
              <span className="ml-1 shrink-0 text-[10px] text-muted-foreground">(archived)</span>
            )}
          </NavItem>
        ))}
      </div>
    );
  });

  return (
    <nav className="flex flex-col py-3">
      <div className="mb-3">
        <NavItem
          icon={<Settings className="h-3.5 w-3.5" />}
          active={selection.kind === "overview"}
          onClick={() => onSelect("overview")}
        >
          Overview
        </NavItem>
      </div>

      {isProxied ? (
        // Code-first + remote: primitives are DISCOVERED from the live server,
        // listed read-only with a Rediscover action. Code-first also keeps a
        // server.py source entry.
        <>
          <div className="max-h-[60vh] overflow-y-auto">{groupsNode}</div>
          {(server.primitives ?? []).length === 0 && (
            <div className="mb-3 border-t px-3 pt-3 text-xs text-muted-foreground">
              No primitives discovered yet.{" "}
              {isRemote
                ? "Confirm the upstream URL/credential, then Rediscover."
                : "Deploy the server, then Rediscover."}
            </div>
          )}
          <RediscoverButton serverName={server.name} onDone={onMutated} />
          {isCodeMode && (
            <div className="mb-3 border-t pt-3">
              <NavHeading>Source</NavHeading>
              <NavItem
                icon={<FileCode className="h-3.5 w-3.5" />}
                active={selection.kind === "source"}
                onClick={() => onSelect("source")}
              >
                server.py
              </NavItem>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="max-h-[60vh] overflow-y-auto">{groupsNode}</div>
          <div className="px-3 mb-3">
            <Button
              size="sm"
              variant={selection.kind === "primitive-new" ? "default" : "outline"}
              className="w-full justify-start"
              onClick={() => onSelect("primitives:new")}
            >
              <Plus className="mr-1 h-3 w-3" /> Add primitive
            </Button>
          </div>
        </>
      )}

      <div className="border-t pt-3">
        <NavHeading>Configuration</NavHeading>
        {isRemote && (
          <NavItem
            icon={<Globe className="h-3.5 w-3.5" />}
            active={selection.kind === "remote"}
            onClick={() => onSelect("remote")}
          >
            Upstream
          </NavItem>
        )}
        {!isProxied && (
          <NavItem
            icon={<FileCode className="h-3.5 w-3.5" />}
            active={selection.kind === "imports"}
            onClick={() => onSelect("imports")}
          >
            Imports &amp; globals
          </NavItem>
        )}
        {!isRemote && (
          <>
            <NavItem
              icon={<Package className="h-3.5 w-3.5" />}
              active={selection.kind === "packages"}
              onClick={() => onSelect("packages")}
            >
              PyPI packages
            </NavItem>
            <NavItem
              icon={<Boxes className="h-3.5 w-3.5" />}
              active={selection.kind === "apt-packages"}
              onClick={() => onSelect("apt-packages")}
            >
              OS packages
            </NavItem>
          </>
        )}
        <NavItem
          icon={<Variable className="h-3.5 w-3.5" />}
          active={selection.kind === "env"}
          onClick={() => onSelect("env")}
        >
          Env vars
        </NavItem>
        <NavItem
          icon={<KeyRound className="h-3.5 w-3.5" />}
          active={selection.kind === "auth"}
          onClick={() => onSelect("auth")}
        >
          Auth
        </NavItem>
        <NavItem
          icon={<Files className="h-3.5 w-3.5" />}
          active={selection.kind === "assets"}
          onClick={() => onSelect("assets")}
        >
          Assets
        </NavItem>
        <NavItem
          icon={<Activity className="h-3.5 w-3.5" />}
          active={selection.kind === "usage"}
          onClick={() => onSelect("usage")}
        >
          Usage
        </NavItem>
        <NavItem
          icon={<FileText className="h-3.5 w-3.5" />}
          active={selection.kind === "logs"}
          onClick={() => onSelect("logs")}
        >
          Logs
        </NavItem>
      </div>
    </nav>
  );
}

function NavHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
    </div>
  );
}

function NavItem({
  active,
  onClick,
  icon,
  children,
}: {
  active?: boolean;
  onClick: () => void;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full min-w-0 items-center gap-2 px-3 py-1.5 text-left transition-colors ${
        active
          ? "bg-primary/10 text-primary border-l-2 border-primary"
          : "border-l-2 border-transparent hover:bg-muted"
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

/**
 * IDE-style editing surface for a structured MCP server.
 *
 * Layout:
 *   ┌──────────┬─────────────────────────────┬──────────────────┐
 *   │ Left nav │ Read-only server.py preview │ Selection form   │
 *   │  primi-  │  highlights selected block; │  per-selection   │
 *   │  tives + │  toggles to Dockerfile for  │  edit form       │
 *   │  config  │  OS-packages context        │  (right rail)    │
 *   └──────────┴─────────────────────────────┴──────────────────┘
 *
 * Selection state lives in the URL so primitives are deep-linkable
 * (/servers/{name}/edit/tools/print_env, /edit/imports, etc.).
 */
export function ServerEdit({ serverName }: ServerEditProps) {
  const navigate = useNavigate();
  const params = useParams();
  // Wildcard after /servers/:name/ — react-router passes the rest as params["*"].
  const selectionPath = params["*"] ?? "";
  const selection = useMemo(() => parseSelection(selectionPath), [selectionPath]);

  const [server, setServer] = useState<Server | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const goto = useCallback(
    (path: string) => navigate(`/servers/${encodeURIComponent(serverName)}/${path}`),
    [navigate, serverName],
  );

  const refresh = useCallback(async () => {
    try {
      const data = await api.getServer(serverName);
      setServer(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load server");
    } finally {
      setLoading(false);
    }
  }, [serverName]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh] text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading {serverName}...
      </div>
    );
  }

  if (error || !server) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        {error ?? "Server not found"}
      </div>
    );
  }

  return (
    <div className="flex flex-col -mx-4 sm:-mx-6 lg:-mx-8">
      {/* Header */}
      <div className="flex items-center gap-3 border-b px-4 sm:px-6 lg:px-8 py-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate("/servers")}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> Servers
        </Button>
        <h2 className="text-lg font-semibold">{server.name}</h2>
        <StatusBadge status={server.status} health={server.health} />
        <code className="ml-auto rounded bg-muted px-2 py-1 text-xs font-mono">
          {server.url}
        </code>
      </div>

      <RedeployBanner serverName={serverName} server={server} onRedeployed={refresh} />

      {/* Two-pane body: nav + editor. No fixed viewport height - the page
          scrolls naturally as a whole; nav and form share the same scroll. */}
      <div className="grid" style={{ gridTemplateColumns: "260px 1fr" }}>
        <aside className="border-r text-sm">
          <LeftNav
            server={server}
            selection={selection}
            onSelect={goto}
            onMutated={refresh}
          />
        </aside>
        <main className="px-6 py-4 text-sm min-w-0">
          <RightRail
            serverName={serverName}
            server={server}
            selection={selection}
            onSaved={() => {
              refresh();
            }}
            onDeleted={() => navigate("/servers")}
            gotoPrimitive={(name) => goto(`primitives/${encodeURIComponent(name)}`)}
          />
        </main>
      </div>
    </div>
  );
}
