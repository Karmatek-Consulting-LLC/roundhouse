export interface ToolParameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default: string | null;
}

export interface ToolPrimitive {
  kind: "tool";
  name: string;
  description: string;
  parameters: ToolParameter[];
  code: string;
  /** FastMCP: str → structured { result }; dict → structured object matches your keys */
  return_type?: "str" | "dict";
}

export interface ResourcePrimitive {
  kind: "resource";
  name: string;
  uri: string;
  description: string;
  mime_type: string;
  code: string;
}

export interface ResourceTemplatePrimitive {
  kind: "resource_template";
  name: string;
  uri_template: string;
  description: string;
  mime_type: string;
  code: string;
}

export interface PromptPrimitive {
  kind: "prompt";
  name: string;
  description: string;
  parameters: ToolParameter[];
  code: string;
}

export type Primitive =
  | ToolPrimitive
  | ResourcePrimitive
  | ResourceTemplatePrimitive
  | PromptPrimitive;

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

export interface EnvVar {
  name: string;
  value: string;
}

export interface PlacementTask {
  task_id: string;
  node_id: string;
  node_name: string | null;
  state: string;
  slot: number | null;
  error: string | null;
}

export interface Server {
  name: string;
  template: string;
  status: string;
  url: string;
  description: string;
  imports: string[];
  primitives: Primitive[];
  pip_packages: string[];
  env_vars: EnvVar[];
  owner_id: string | null;
  owner_email: string | null;
  created_at: string | null;
  replicas_desired: number;
  replicas_running: number;
  docker_swarm_mode: boolean;
  placement: PlacementTask[];
}

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
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
}

const BASE = "/api";

async function errorDetailFrom401(res: Response): Promise<string | undefined> {
  const body = await res.json().catch(() => ({}));
  const d = (body as { detail?: unknown }).detail;
  return typeof d === "string" ? d : undefined;
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
    localStorage.removeItem("token");
    throw new Error(detail ?? "Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed: ${res.status}`);
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
    localStorage.removeItem("token");
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
  getServer: (name: string) => request<Server>(`/servers/${name}`),
  getServerLogs: (name: string, tail = 200) =>
    requestText(`/servers/${encodeURIComponent(name)}/logs?tail=${tail}`),
  createServer: (data: CreateServerRequest) =>
    request<Server>("/servers", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  startServer: (name: string) =>
    request<Server>(`/servers/${name}/start`, { method: "POST" }),
  stopServer: (name: string) =>
    request<Server>(`/servers/${name}/stop`, { method: "POST" }),
  redeployServer: (name: string) =>
    request<Server>(`/servers/${name}/redeploy`, { method: "POST" }),
  deleteServer: (name: string) =>
    request<void>(`/servers/${name}`, { method: "DELETE" }),

  updateDescription: (serverName: string, description: string) =>
    request<Server>(`/servers/${serverName}/description`, {
      method: "PUT",
      body: JSON.stringify({ description }),
    }),
  updateServerReplicas: (serverName: string, replicas: number) =>
    request<Server>(`/servers/${serverName}/replicas`, {
      method: "PUT",
      body: JSON.stringify({ replicas }),
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
  updatePipPackages: (serverName: string, pip_packages: string[]) =>
    request<Server>(`/servers/${serverName}/packages`, {
      method: "PUT",
      body: JSON.stringify({ pip_packages }),
    }),
  updateEnvVars: (serverName: string, env_vars: EnvVar[]) =>
    request<Server>(`/servers/${serverName}/env`, {
      method: "PUT",
      body: JSON.stringify({ env_vars }),
    }),
  deployConfig: (serverName: string, imports: string[], pip_packages: string[], env_vars: EnvVar[]) =>
    request<Server>(`/servers/${serverName}/config`, {
      method: "PUT",
      body: JSON.stringify({ imports, pip_packages, env_vars }),
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
  register: (data: { email: string; password: string; display_name: string; role?: string }) =>
    request<AuthUser>("/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Users
  listUsers: () => request<AuthUser[]>("/users"),
  deleteUser: (id: string) =>
    request<void>(`/users/${id}`, { method: "DELETE" }),

  // Settings
  getSettings: () => request<{
    hostname: string;
    tls_enabled: boolean;
    has_certificate: boolean;
    base_url: string;
    default_mcp_server_replicas: number;
    max_mcp_server_replicas: number;
    docker_swarm_mode: boolean;
    docker_registry: string;
    docker_registry_effective: string;
    docker_registry_username: string;
    docker_registry_password_configured: boolean;
  }>("/settings"),
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
  updateHostname: async (hostname: string) => {
    const token = localStorage.getItem("token");
    const form = new FormData();
    form.append("hostname", hostname);
    const res = await fetch("/api/settings/hostname", {
      method: "PUT",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!res.ok) throw new Error("Failed to update hostname");
    return res.json();
  },
  uploadCertificate: async (cert: File, key: File) => {
    const token = localStorage.getItem("token");
    const form = new FormData();
    form.append("cert", cert);
    form.append("key", key);
    const res = await fetch("/api/settings/certificate", {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? "Failed to upload certificate");
    }
    return res.json();
  },
  deleteCertificate: () =>
    request<{ tls_enabled: boolean }>("/settings/certificate", { method: "DELETE" }),

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
};
