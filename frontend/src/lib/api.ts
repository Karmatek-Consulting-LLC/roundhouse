export interface ToolParameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default: string | null;
}

/** Per-primitive overrides for the platform middleware (rate limit,
 * concurrency cap, request logging). Keys with undefined values inherit
 * server-level defaults. */
export interface PrimitiveMiddleware {
  rate_limit_rpm?: number;
  max_concurrent?: number;
  max_argument_bytes?: number;
  log_arguments?: boolean;
  log_calls?: boolean;
}

export interface ToolPrimitive {
  kind: "tool";
  name: string;
  description: string;
  parameters: ToolParameter[];
  code: string;
  /** FastMCP: str → structured { result }; dict → structured object matches your keys */
  return_type?: "str" | "dict";
  /** Scope names required to invoke. Empty/absent = no scope check (token-only). */
  scopes?: string[];
  middleware?: PrimitiveMiddleware;
  /** Discovered via proxy introspection (code/remote); its schema is read-only
   * and only scopes are operator-editable. Absent on authored (structured). */
  discovered?: boolean;
  /** Previously discovered but no longer present upstream (kept, not deleted). */
  archived?: boolean;
}

export interface ResourcePrimitive {
  kind: "resource";
  name: string;
  uri: string;
  description: string;
  mime_type: string;
  code: string;
  scopes?: string[];
  middleware?: PrimitiveMiddleware;
  /** Discovered via proxy introspection (code/remote); its schema is read-only
   * and only scopes are operator-editable. Absent on authored (structured). */
  discovered?: boolean;
  /** Previously discovered but no longer present upstream (kept, not deleted). */
  archived?: boolean;
}

export interface ResourceTemplatePrimitive {
  kind: "resource_template";
  name: string;
  uri_template: string;
  description: string;
  mime_type: string;
  code: string;
  scopes?: string[];
  middleware?: PrimitiveMiddleware;
  /** Discovered via proxy introspection (code/remote); its schema is read-only
   * and only scopes are operator-editable. Absent on authored (structured). */
  discovered?: boolean;
  /** Previously discovered but no longer present upstream (kept, not deleted). */
  archived?: boolean;
}

export interface PromptPrimitive {
  kind: "prompt";
  name: string;
  description: string;
  parameters: ToolParameter[];
  code: string;
  scopes?: string[];
  middleware?: PrimitiveMiddleware;
  /** Discovered via proxy introspection (code/remote); its schema is read-only
   * and only scopes are operator-editable. Absent on authored (structured). */
  discovered?: boolean;
  /** Previously discovered but no longer present upstream (kept, not deleted). */
  archived?: boolean;
}

export type Primitive =
  | ToolPrimitive
  | ResourcePrimitive
  | ResourceTemplatePrimitive
  | PromptPrimitive;

export interface UsagePrimitive {
  kind: string;
  name: string;
  calls: number;
  errors: number;
  last_call_ts: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  samples: number;
}

export interface UsageClient {
  name: string;
  client_id: string | null;
  calls: number;
  last_call_ts: number;
}

export interface Asset {
  name: string;
  size: number;
  modified_ts: number;
}

export interface AssetListResponse {
  assets: Asset[];
  total_size: number;
  max_file_bytes: number;
  max_total_bytes: number;
}

export interface UsageSnapshot {
  primitives: UsagePrimitive[];
  tokens: UsageClient[];
  started_ts: number;
  now_ts: number;
  available: boolean;
}

export interface DashboardTopServer {
  name: string;
  calls: number;
  errors: number;
  p95_ms: number | null;
}

/** Platform-wide usage rollup. Point-in-time: each fetch fans out a live
 * /metrics scrape across the caller's running servers - no history. */
export interface DashboardUsage {
  running_servers: number;
  scraped_servers: number;
  total_calls: number;
  total_errors: number;
  error_rate: number;
  by_kind: Record<string, number>;
  top_servers: DashboardTopServer[];
}

// ---- Observability (persistent request history for the Observe console) ----

export type ObsRange = "5m" | "15m" | "1h" | "6h" | "24h" | "7d";
export type ObsBucket = "auto" | "10s" | "30s" | "1m" | "5m" | "10m" | "1h";

export interface ObsKindCounts {
  tool: number;
  resource: number;
  resource_template: number;
  prompt: number;
}

export interface ObsTimeseriesBucket {
  /** Bucket start, epoch seconds. */
  ts: number;
  calls: number;
  errors: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  by_kind: ObsKindCounts;
}

export interface ObsTimeseries {
  buckets: ObsTimeseriesBucket[];
  bucket_s: number;
}

export type ObsStatus = "ok" | "error";

export interface ObsEvent {
  id: number;
  /** Epoch seconds. */
  ts: number;
  server_name: string;
  kind: string;
  name: string;
  client_id: string | null;
  duration_ms: number | null;
  status: ObsStatus;
  error: string | null;
}

export interface ObsFeedPage {
  events: ObsEvent[];
  last_id: number;
}

export interface ObsRankedItem {
  key: string;
  label: string;
  calls: number;
  errors: number;
  p95_ms: number | null;
}

export interface ObsTop {
  by: "tool" | "server" | "client";
  ranked: ObsRankedItem[];
  error_leaders: ObsRankedItem[];
  latency_leaders: ObsRankedItem[];
}

export interface TemplateVariable {
  name: string;
  description: string;
  default: string | null;
  required: boolean;
}

export interface Template {
  name: string;
  description: string;
  variables: TemplateVariable[];
}

/** When `secret` is true, the platform stores the value encrypted at rest
 * (laravel-crypto) and never echoes it back. The editor shows the row as
 * masked; sending `value: ""` on save preserves the stored ciphertext. */
export interface EnvVar {
  name: string;
  value: string;
  secret?: boolean;
  has_value?: boolean;
}

/** Server env: explicit global imports + local pairs (API `/servers/.../config` and `/env`). */
export interface ServerEnvConfig {
  env_global_imports: string[];
  env_vars: EnvVar[];
}

// --- Live MCP invocation result shapes (mirrors FastMCP's JSON-RPC responses) ---

export interface McpContentBlock {
  type: "text" | "image" | "resource" | string;
  text?: string;
  data?: string;
  mimeType?: string;
  [key: string]: unknown;
}

export interface McpToolResult {
  content: McpContentBlock[];
  structuredContent?: unknown;
  isError?: boolean;
  _meta?: Record<string, unknown>;
}

export interface McpResourceResult {
  contents: Array<{
    uri: string;
    mimeType?: string;
    text?: string;
    blob?: string;
  }>;
}

export interface McpPromptResult {
  description?: string;
  messages: Array<{
    role: string;
    content: McpContentBlock | string;
  }>;
}

// --- Live schema from tools/list | resources/list | prompts/list ---

export interface McpJsonSchema {
  type?: string;
  properties?: Record<string, {
    type?: string;
    description?: string;
    default?: unknown;
    [key: string]: unknown;
  }>;
  required?: string[];
  [key: string]: unknown;
}

export interface McpLiveTool {
  name: string;
  description?: string;
  inputSchema?: McpJsonSchema;
}

export interface McpLiveResource {
  uri?: string;
  uriTemplate?: string;
  name: string;
  description?: string;
  mimeType?: string;
  isTemplate?: boolean;
}

export interface McpLivePrompt {
  name: string;
  description?: string;
  arguments?: Array<{
    name: string;
    description?: string;
    required?: boolean;
  }>;
}

export interface PlacementTask {
  task_id: string;
  node_id: string;
  node_name: string | null;
  state: string;
  slot: number | null;
  error: string | null;
}

/** A node-label key=value selector chosen for Swarm placement. Translated
 * server-side into a `node.labels.<key>==<value>` service constraint. */
export interface PlacementConstraint {
  key: string;
  value: string;
}

/** A distinct node-label pair available for placement selection, with the
 * count of swarm nodes currently carrying it. */
export interface NodeLabel {
  key: string;
  value: string;
  nodes: number;
}

export type ServerMode = "structured" | "code" | "remote";

/** Outbound header sent to a remote upstream. The mapping (header -> env var
 * holding the secret value) is what's persisted/returned; the secret value
 * itself is write-only (sent on create, never echoed back). */
export interface RemoteHeaderMapping {
  header: string;
  env: string;
}

export interface AuditEvent {
  id: number;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string;
  target_id: string;
  payload: Record<string, unknown> | null;
  created_at: string | null;
}

// ---- Backup & restore (superadmin) ----

export interface BackupCounts {
  servers: number;
  users: number;
  server_tokens: number;
}

/** Status of the self-managed HTTPS certificate (Traefik terminates TLS here).
 * PEM contents never come back over the wire — only presence + leaf metadata. */
export interface TlsCertStatus {
  /** Deployment opted into self-managed TLS (MCP_TLS_SELF_MANAGED); gates the UI. */
  supported: boolean;
  configured: boolean;
  subject_cn?: string;
  issuer_cn?: string;
  sans?: string[];
  not_before?: string;
  not_after?: string;
}

/** Live deployment summary shown before an export / compared against on restore. */
export interface DeploymentInfo {
  postgres: boolean;
  alembic_revision: string | null;
  app_key_fingerprint: string | null;
  base_url: string;
  orchestrator: string;
  counts: BackupCounts;
}

/** Metadata embedded in a backup archive's manifest.json. */
export interface BackupManifest {
  format_version: number;
  created_at: string;
  alembic_revision: string | null;
  app_key_fingerprint: string | null;
  base_url: string;
  orchestrator: string;
  pg_dump_format: string;
  counts: BackupCounts;
}

/** Dry-run validation of an uploaded backup. `problems` empty = safe to apply. */
export interface RestorePreview {
  manifest: BackupManifest;
  problems: string[];
  current: DeploymentInfo;
}

export interface ReconcileError {
  server: string;
  op: string;
  error: string;
}

/** Outcome of making live workloads match the restored database. */
export interface ReconcileSummary {
  redeployed: string[];
  reaped: string[];
  errors: ReconcileError[];
}

export interface RestoreResult {
  manifest: BackupManifest;
  problems: string[];
  forced: boolean;
  reconcile: ReconcileSummary;
}

/** Health values come from Docker HEALTHCHECK. `starting` while the
 * grace period hasn't elapsed; `healthy` once the probe has succeeded;
 * `unhealthy` after retry failures. `null` when no healthcheck is defined
 * (older images, swarm services). */
export type ServerHealth = "starting" | "healthy" | "unhealthy" | null;

export interface Server {
  name: string;
  template: string;
  status: string;
  health?: ServerHealth;
  restart_count?: number | null;
  url: string;
  description: string;
  mode: ServerMode;
  source: string | null;
  imports: string[];
  primitives: Primitive[];
  pip_packages: string[];
  /** OS-level (apt) packages installed into the container image before pip. */
  apt_packages: string[];
  env_global_imports?: string[];
  env_vars: EnvVar[];
  /** Platform-wide catalog for picking global imports. */
  global_env?: EnvVar[];
  owner_id: string | null;
  owner_email: string | null;
  created_at: string | null;
  replicas_desired: number;
  replicas_running: number;
  docker_swarm_mode: boolean;
  placement: PlacementTask[];
  /** Desired Swarm node-label placement selectors (input; all ANDed). Distinct
   * from `placement`, which is where tasks currently run (output). */
  placement_constraints?: PlacementConstraint[];
  /** ISO-8601 timestamp set when scope/token changes need a redeploy; null otherwise. */
  redeploy_required_at?: string | null;
  /** Docker --cpus value (whole CPUs, fractional ok). null = no cap. */
  cpu_limit?: number | null;
  /** Docker --memory in MB. null = no cap. */
  memory_limit_mb?: number | null;
  /** Set when the server was imported via "Deploy from Git"; enables "Update from Git". */
  git_url?: string | null;
  git_ref?: string | null;
  /** Remote-proxy (mode === "remote") upstream MCP URL. */
  remote_url?: string | null;
  /** Outbound header -> env mappings (secret values never returned). */
  remote_headers?: RemoteHeaderMapping[];
  /** When true, tools with no assigned scope are denied (remote default). */
  deny_unlisted?: boolean;
}

export interface ServerScope {
  id: number;
  name: string;
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ServerTokenSummary {
  id: number;
  name: string;
  display_prefix: string;
  scopes: string[];
  created_at: string | null;
}

/** mintToken response: same shape as ServerTokenSummary plus the one-time plaintext. */
export interface MintedToken extends ServerTokenSummary {
  token: string;
}

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  /** "local" (password / break-glass) or "entra" (SSO). Absent on older API. */
  auth_source?: "local" | "entra";
}

export interface RoleMapping {
  id: number;
  entra_app_role: string;
  roundhouse_role: "superadmin" | "user";
  team_id: string | null;
  team_role: "admin" | "member";
}

export interface SsoConfig {
  entra_tenant_id: string;
  entra_client_id: string;
  /** The secret is never returned — only whether one is stored. */
  entra_client_secret_configured: boolean;
  /** Read-only: derived from the public base URL (PUBLIC_HOSTNAME). */
  entra_redirect_uri: string;
  /** When true, a first SSO login adopts a matching local account by email. */
  link_local_by_email: boolean;
  enabled: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface TeamMember {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
}

export interface Team {
  id: string;
  name: string;
  description: string;
  members: TeamMember[];
}

export interface PyPIPackageInfo {
  name: string;
  version: string;
  summary: string;
}

export interface CreateServerRequest {
  name: string;
  description?: string;
  template?: string;
  config?: Record<string, string>;
  /** Omit to use platform default (Swarm only for N>1). */
  replicas?: number | null;
  /** "structured" (default) - primitives managed via the UI.
   *  "code"       - user provides a full server.py; primitive editor is hidden.
   *  "remote"     - proxy an external MCP server (remote_url + remote_headers). */
  mode?: ServerMode;
  /** Required when mode === "code". The raw server.py text. */
  source?: string;
  /** Required when mode === "remote". The upstream MCP endpoint URL. */
  remote_url?: string;
  /** Outbound headers for mode === "remote". Values are secret (write-only). */
  remote_headers?: { header: string; value: string }[];
  /** Swarm node-label placement selectors. Validated against existing labels. */
  placement_constraints?: PlacementConstraint[];
}

const BASE = "/api";

// Full-page navigation target for "Sign in with Microsoft". This is a browser
// redirect (the server bounces it to Entra), NOT a fetch, so it is a plain URL.
export const OIDC_LOGIN_URL = `${BASE}/auth/oidc/login`;

function formatApiErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (
          item &&
          typeof item === "object" &&
          "msg" in item &&
          typeof (item as { msg: unknown }).msg === "string"
        ) {
          return (item as { msg: string }).msg;
        }
        return JSON.stringify(item);
      })
      .join(", ");
  }
  if (detail != null && typeof detail === "object") return JSON.stringify(detail);
  return "Request failed";
}

async function errorDetailFrom401(res: Response): Promise<string | undefined> {
  const body = await res.json().catch(() => ({}));
  const d = (body as { detail?: unknown }).detail;
  return typeof d === "string" ? d : undefined;
}

// Broadcast a session-expired event so AuthProvider can clear React state
// and let the router redirect to /login. Removing the token from
// localStorage alone is not enough - the auth context only reads it on mount.
export const AUTH_EXPIRED_EVENT = "auth:expired";

function signalSessionExpired() {
  localStorage.removeItem("token");
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE}${path}`, { headers, ...options });
  if (res.status === 401) {
    const detail = await errorDetailFrom401(res);
    const method = (options?.method ?? "GET").toUpperCase();
    const isLoginFailure = path === "/auth/login" && method === "POST";
    if (isLoginFailure) {
      throw new Error(detail ?? "Invalid email or password");
    }
    signalSessionExpired();
    throw new Error(detail ?? "Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const d = (body as { detail?: unknown }).detail;
    throw new Error(d != null ? formatApiErrorDetail(d) : `Request failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function requestText(path: string): Promise<string> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE}${path}`, { headers });
  if (res.status === 401) {
    signalSessionExpired();
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const text = await res.text();
    try {
      const body = JSON.parse(text) as { detail?: string };
      throw new Error(body.detail ?? (text || `Request failed: ${res.status}`));
    } catch (e) {
      if (e instanceof SyntaxError) {
        throw new Error(text || `Request failed: ${res.status}`);
      }
      throw e;
    }
  }
  return res.text();
}

export const api = {
  listTemplates: () => request<Template[]>("/templates"),
  getTemplate: (name: string) => request<Template>(`/templates/${name}`),
  listServers: () => request<Server[]>("/servers"),
  getServerReplicaLimits: () =>
    request<{
      default_mcp_server_replicas: number;
      max_mcp_server_replicas: number;
      docker_swarm_mode: boolean;
    }>("/servers/limits"),
  /** Node-label pairs available for Swarm placement selection (derived from
   * actual node labels, not free-form). `supported` is false off Swarm. */
  listNodeLabels: () =>
    request<{ supported: boolean; labels: NodeLabel[] }>("/servers/node-labels"),
  getServer: (name: string) => request<Server>(`/servers/${name}`),
  getServerLogs: (name: string, tail = 200) =>
    requestText(`/servers/${encodeURIComponent(name)}/logs?tail=${tail}`),
  getServerUsage: (name: string) =>
    request<UsageSnapshot>(`/servers/${encodeURIComponent(name)}/usage`),
  getDashboardUsage: () => request<DashboardUsage>("/dashboard/usage"),

  // Observability console (persistent request history)
  getObsTimeseries: (p: { range: ObsRange; bucket?: ObsBucket; server?: string }) => {
    const q = new URLSearchParams({ range: p.range });
    if (p.bucket) q.set("bucket", p.bucket);
    if (p.server) q.set("server", p.server);
    return request<ObsTimeseries>(`/observability/timeseries?${q.toString()}`);
  },
  getObsFeed: (p: { since_id?: number; server?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (p.since_id != null) q.set("since_id", String(p.since_id));
    if (p.server) q.set("server", p.server);
    if (p.limit != null) q.set("limit", String(p.limit));
    const s = q.toString();
    return request<ObsFeedPage>(`/observability/feed${s ? `?${s}` : ""}`);
  },
  getObsTop: (p: { range: ObsRange; by: "tool" | "server" | "client"; server?: string }) => {
    const q = new URLSearchParams({ range: p.range, by: p.by });
    if (p.server) q.set("server", p.server);
    return request<ObsTop>(`/observability/top?${q.toString()}`);
  },
  listAssets: (serverName: string) =>
    request<AssetListResponse>(`/servers/${encodeURIComponent(serverName)}/assets`),
  uploadAsset: async (serverName: string, file: File): Promise<Asset> => {
    const token = localStorage.getItem("token");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    // No Content-Type: fetch auto-sets multipart/form-data with the right boundary.
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`${BASE}/servers/${encodeURIComponent(serverName)}/assets`, {
      method: "POST",
      headers,
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const d = (body as { detail?: unknown }).detail;
      throw new Error(d != null ? formatApiErrorDetail(d) : `Upload failed: ${res.status}`);
    }
    return res.json();
  },
  deleteAsset: (serverName: string, filename: string) =>
    request<void>(`/servers/${encodeURIComponent(serverName)}/assets/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),
  downloadAsset: async (serverName: string, filename: string): Promise<Blob> => {
    const token = localStorage.getItem("token");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(
      `${BASE}/servers/${encodeURIComponent(serverName)}/assets/${encodeURIComponent(filename)}`,
      { headers },
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const d = (body as { detail?: unknown }).detail;
      throw new Error(d != null ? formatApiErrorDetail(d) : `Download failed: ${res.status}`);
    }
    return res.blob();
  },
  createServer: (data: CreateServerRequest) =>
    request<Server>("/servers", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deployFromGit: (data: {
    name: string;
    git_url: string;
    ref?: string;
    description?: string;
    replicas?: number;
    placement_constraints?: PlacementConstraint[];
  }) =>
    request<Server>("/servers/from-git", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  /** Download the server's export bundle: a zip of manifest.json (the spec),
   * assets/, and any git/template build files. */
  exportServer: async (name: string): Promise<Blob> => {
    const token = localStorage.getItem("token");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${BASE}/servers/${encodeURIComponent(name)}/export`, { headers });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const d = (body as { detail?: unknown }).detail;
      throw new Error(d != null ? formatApiErrorDetail(d) : `Export failed: ${res.status}`);
    }
    return res.blob();
  },
  /** Legacy JSON import — a bare spec or a v1 export envelope's `spec`. */
  importServer: (data: { spec: Record<string, unknown>; name_override?: string }) =>
    request<Server>("/servers/import", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  /** Import a zip bundle produced by exportServer. */
  importServerArchive: async (file: File, nameOverride?: string): Promise<Server> => {
    const token = localStorage.getItem("token");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    // No Content-Type: fetch auto-sets multipart/form-data with the right boundary.
    const form = new FormData();
    form.append("file", file, file.name);
    if (nameOverride) form.append("name_override", nameOverride);
    const res = await fetch(`${BASE}/servers/import-archive`, {
      method: "POST",
      headers,
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const d = (body as { detail?: unknown }).detail;
      throw new Error(d != null ? formatApiErrorDetail(d) : `Import failed: ${res.status}`);
    }
    return res.json() as Promise<Server>;
  },

  // Audit log (superadmin only)
  listAuditEvents: (params: { target_type?: string; target_id?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.target_type) q.set("target_type", params.target_type);
    if (params.target_id) q.set("target_id", params.target_id);
    if (params.limit) q.set("limit", String(params.limit));
    const s = q.toString();
    return request<AuditEvent[]>(`/audit${s ? `?${s}` : ""}`);
  },
  startServer: (name: string) =>
    request<Server>(`/servers/${name}/start`, { method: "POST" }),
  stopServer: (name: string) =>
    request<Server>(`/servers/${name}/stop`, { method: "POST" }),
  redeployServer: (name: string) =>
    request<Server>(`/servers/${name}/redeploy`, { method: "POST" }),
  /** Re-introspect a proxied server (code-first or remote) and reconcile its
   * primitives. Flags a redeploy. Code-first must be deployed/running first. */
  rediscoverServer: (name: string) =>
    request<Server>(`/servers/${encodeURIComponent(name)}/rediscover`, { method: "POST" }),
  updateFromGit: (name: string) =>
    request<Server>(`/servers/${encodeURIComponent(name)}/update-from-git`, { method: "POST" }),
  deleteServer: (name: string) =>
    request<void>(`/servers/${name}`, { method: "DELETE" }),

  updateDescription: (serverName: string, description: string) =>
    request<Server>(`/servers/${serverName}/description`, {
      method: "PUT",
      body: JSON.stringify({ description }),
    }),
  updateServerResources: (
    serverName: string,
    body: { cpu_limit: number | null; memory_limit_mb: number | null },
  ) =>
    request<Server>(`/servers/${serverName}/resources`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  updateServerReplicas: (serverName: string, replicas: number) =>
    request<Server>(`/servers/${serverName}/replicas`, {
      method: "PUT",
      body: JSON.stringify({ replicas }),
    }),
  updateServerPlacement: (serverName: string, placement_constraints: PlacementConstraint[]) =>
    request<Server>(`/servers/${serverName}/placement`, {
      method: "PUT",
      body: JSON.stringify({ placement_constraints }),
    }),

  // Primitives
  addPrimitive: (serverName: string, primitive: Primitive) =>
    request<Server>(`/servers/${serverName}/primitives`, {
      method: "POST",
      body: JSON.stringify({ primitive }),
    }),
  updatePrimitive: (serverName: string, primName: string, primitive: Primitive) =>
    request<Server>(`/servers/${serverName}/primitives/${primName}`, {
      method: "PUT",
      body: JSON.stringify({ primitive }),
    }),
  deletePrimitive: (serverName: string, primName: string) =>
    request<Server>(`/servers/${serverName}/primitives/${primName}`, {
      method: "DELETE",
    }),
  /** Permanently remove all archived (vanished-upstream) primitives from a
   * server. Allowed on proxied servers; archived entries are already excluded
   * from the live toolset, so this only clears the management view. */
  clearArchivedPrimitives: (serverName: string) =>
    request<Server>(`/servers/${encodeURIComponent(serverName)}/primitives-archived`, {
      method: "DELETE",
    }),
  updatePipPackages: (serverName: string, pip_packages: string[]) =>
    request<Server>(`/servers/${serverName}/packages`, {
      method: "PUT",
      body: JSON.stringify({ pip_packages }),
    }),
  updateAptPackages: (serverName: string, apt_packages: string[]) =>
    request<Server>(`/servers/${serverName}/apt-packages`, {
      method: "PUT",
      body: JSON.stringify({ apt_packages }),
    }),
  updateEnvVars: (serverName: string, cfg: ServerEnvConfig) =>
    request<Server>(`/servers/${serverName}/env`, {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),
  deployConfig: (
    serverName: string,
    imports: string[],
    pip_packages: string[],
    apt_packages: string[],
    env: ServerEnvConfig,
  ) =>
    request<Server>(`/servers/${serverName}/config`, {
      method: "PUT",
      body: JSON.stringify({
        imports,
        pip_packages,
        apt_packages,
        env_global_imports: env.env_global_imports,
        env_vars: env.env_vars,
      }),
    }),

  updateSource: (serverName: string, source: string) =>
    request<Server>(`/servers/${encodeURIComponent(serverName)}/source`, {
      method: "PUT",
      body: JSON.stringify({ source }),
    }),

  // Live MCP primitive discovery - used by code-mode servers where our stored spec has no primitives.
  listLiveTools: (serverName: string) =>
    request<{ tools: McpLiveTool[] }>(`/servers/${encodeURIComponent(serverName)}/tools`),
  listLiveResources: (serverName: string) =>
    request<{ resources: McpLiveResource[] }>(
      `/servers/${encodeURIComponent(serverName)}/resources`,
    ),
  listLivePrompts: (serverName: string) =>
    request<{ prompts: McpLivePrompt[] }>(`/servers/${encodeURIComponent(serverName)}/prompts`),

  // Live MCP invocation (pass-through to the deployed server's JSON-RPC endpoint).
  // tokenName picks which server token the backend attaches to the internal
  // call (omitted -> the oldest token, if any); the plaintext never reaches the UI.
  invokeTool: (serverName: string, tool: string, args: Record<string, unknown>, tokenName?: string) =>
    request<McpToolResult>(`/servers/${encodeURIComponent(serverName)}/tools/invoke`, {
      method: "POST",
      body: JSON.stringify({ tool, arguments: args, ...(tokenName ? { token_name: tokenName } : {}) }),
    }),
  readResource: (serverName: string, uri: string, tokenName?: string) =>
    request<McpResourceResult>(`/servers/${encodeURIComponent(serverName)}/resources/read`, {
      method: "POST",
      body: JSON.stringify({ uri, ...(tokenName ? { token_name: tokenName } : {}) }),
    }),
  getPrompt: (serverName: string, prompt: string, args: Record<string, unknown>, tokenName?: string) =>
    request<McpPromptResult>(`/servers/${encodeURIComponent(serverName)}/prompts/get`, {
      method: "POST",
      body: JSON.stringify({ prompt, arguments: args, ...(tokenName ? { token_name: tokenName } : {}) }),
    }),

  // Per-server runtime auth (scopes + tokens for the FastMCP StaticTokenVerifier).
  listScopes: (serverName: string) =>
    request<ServerScope[]>(`/servers/${encodeURIComponent(serverName)}/scopes`),
  createScope: (serverName: string, body: { name: string; description?: string | null }) =>
    request<ServerScope>(`/servers/${encodeURIComponent(serverName)}/scopes`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateScope: (
    serverName: string,
    scopeName: string,
    body: { name?: string; description?: string | null },
  ) =>
    request<ServerScope>(
      `/servers/${encodeURIComponent(serverName)}/scopes/${encodeURIComponent(scopeName)}`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  deleteScope: (serverName: string, scopeName: string) =>
    request<void>(
      `/servers/${encodeURIComponent(serverName)}/scopes/${encodeURIComponent(scopeName)}`,
      { method: "DELETE" },
    ),
  listTokens: (serverName: string) =>
    request<ServerTokenSummary[]>(`/servers/${encodeURIComponent(serverName)}/tokens`),
  mintToken: (serverName: string, body: { name: string; scopes?: string[] }) =>
    request<MintedToken>(`/servers/${encodeURIComponent(serverName)}/tokens`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeToken: (serverName: string, id: number) =>
    request<void>(`/servers/${encodeURIComponent(serverName)}/tokens/${id}`, {
      method: "DELETE",
    }),

  // PyPI
  searchPyPI: (query: string) =>
    request<PyPIPackageInfo[]>(`/pypi/search?q=${encodeURIComponent(query)}`),

  // Auth
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<AuthUser>("/auth/me"),
  oidcStatus: () => request<{ enabled: boolean }>("/auth/oidc/status"),

  // SSO connection config (superadmin only). Stored in platform settings, not env.
  getSsoConfig: () => request<SsoConfig>("/settings/sso"),
  updateSsoConfig: (body: {
    entra_tenant_id: string;
    entra_client_id: string;
    // Omit to keep the stored secret; "" clears it; any value replaces it.
    entra_client_secret?: string;
    link_local_by_email?: boolean;
  }) =>
    request<SsoConfig>("/settings/sso", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  // SSO role mappings (superadmin only)
  listRoleMappings: () => request<RoleMapping[]>("/role-mappings"),
  // Reconcile the built-in (team-less) role mappings to these Entra app-role lists.
  updateBuiltinRoleMappings: (body: { superadmin: string[]; user: string[] }) =>
    request<RoleMapping[]>("/role-mappings/builtin", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  createRoleMapping: (body: Omit<RoleMapping, "id">) =>
    request<RoleMapping>("/role-mappings", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateRoleMapping: (id: number, body: Omit<RoleMapping, "id">) =>
    request<RoleMapping>(`/role-mappings/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteRoleMapping: (id: number) =>
    request<void>(`/role-mappings/${id}`, { method: "DELETE" }),
  register: (data: { email: string; password: string; display_name: string; role?: string }) =>
    request<AuthUser>("/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  changePassword: (data: { current_password: string; new_password: string }) =>
    request<void>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Users
  listUsers: () => request<AuthUser[]>("/users"),
  updateUser: (
    userId: string,
    data: { role?: "user" | "superadmin"; auth_source?: "local" | "entra" },
  ) =>
    request<AuthUser>(`/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  setUserPassword: (userId: string, new_password: string) =>
    request<void>(`/users/${userId}/password`, {
      method: "PUT",
      body: JSON.stringify({ new_password }),
    }),
  deleteUser: (id: string) =>
    request<void>(`/users/${id}`, { method: "DELETE" }),

  // Settings
  getSettings: () => request<{
    /** Read-only: set at deploy time via MCP_BASE_URL / PUBLIC_HOSTNAME. */
    base_url: string;
    default_mcp_server_replicas: number;
    max_mcp_server_replicas: number;
    docker_swarm_mode: boolean;
    docker_registry: string;
    docker_registry_effective: string;
    docker_registry_username: string;
    docker_registry_password_configured: boolean;
    /** PEM contents never come back over the wire; this just reflects presence. */
    custom_ca_cert_configured: boolean;
    /** How many certificate blocks the stored bundle contains. */
    custom_ca_cert_count: number;
    /** Self-managed HTTPS cert status (terminate TLS on the embedded Traefik). */
    tls_cert: TlsCertStatus;
  }>("/settings"),
  updateCustomCa: (cert: string) =>
    request<{ custom_ca_cert_configured: boolean; cert_count: number }>("/settings/custom-ca", {
      method: "PUT",
      body: JSON.stringify({ cert }),
    }),
  deleteCustomCa: () =>
    request<{ custom_ca_cert_configured: boolean }>("/settings/custom-ca", {
      method: "DELETE",
    }),
  updateTlsCert: (cert: string, key: string) =>
    request<{ tls_cert: TlsCertStatus }>("/settings/tls-cert", {
      method: "PUT",
      body: JSON.stringify({ cert, key }),
    }),
  deleteTlsCert: () =>
    request<{ tls_cert: TlsCertStatus }>("/settings/tls-cert", {
      method: "DELETE",
    }),
  updateDockerRegistry: (body: {
    registry: string;
    username?: string;
    password?: string;
  }) =>
    request<{
      docker_registry: string;
      docker_registry_effective: string;
      docker_registry_username: string;
      docker_registry_password_configured: boolean;
    }>("/settings/docker-registry", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getMcpEnvSettings: () => request<{ env_vars: EnvVar[] }>("/settings/mcp-env"),
  putMcpEnvSettings: (env_vars: EnvVar[]) =>
    request<{ env_vars: EnvVar[] }>("/settings/mcp-env", {
      method: "PUT",
      body: JSON.stringify({ env_vars }),
    }),

  // Teams
  listTeams: () => request<Team[]>("/teams"),
  createTeam: (data: { name: string; description?: string }) =>
    request<Team>("/teams", { method: "POST", body: JSON.stringify(data) }),
  deleteTeam: (id: string) =>
    request<void>(`/teams/${id}`, { method: "DELETE" }),
  addTeamMember: (teamId: string, userId: string, role: string = "member") =>
    request<Team>(`/teams/${teamId}/members`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, role }),
    }),
  removeTeamMember: (teamId: string, userId: string) =>
    request<Team>(`/teams/${teamId}/members/${userId}`, { method: "DELETE" }),

  // Backup & restore (superadmin)
  getBackupInfo: () => request<DeploymentInfo>("/backup/info"),
  exportBackup: async (): Promise<{ blob: Blob; filename: string }> => {
    const token = localStorage.getItem("token");
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${BASE}/backup/export`, { headers });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const d = (body as { detail?: unknown }).detail;
      throw new Error(d != null ? formatApiErrorDetail(d) : `Backup failed: ${res.status}`);
    }
    const cd = res.headers.get("Content-Disposition") ?? "";
    const m = /filename="?([^"]+)"?/.exec(cd);
    const filename = m ? m[1] : "roundhouse-backup.tar.gz";
    return { blob: await res.blob(), filename };
  },
  previewRestore: async (file: File): Promise<RestorePreview> =>
    uploadBackup<RestorePreview>("/backup/restore/preview", file),
  restoreBackup: async (file: File, force = false): Promise<RestoreResult> =>
    uploadBackup<RestoreResult>(`/backup/restore${force ? "?force=true" : ""}`, file),
};

/** Shared multipart POST for backup uploads (preview + restore). Restore can
 * run for minutes while it rebuilds servers, so this has no client timeout. */
async function uploadBackup<T>(path: string, file: File): Promise<T> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(`${BASE}${path}`, { method: "POST", headers, body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const d = (body as { detail?: unknown }).detail;
    throw new Error(d != null ? formatApiErrorDetail(d) : `Request failed: ${res.status}`);
  }
  return res.json();
}
