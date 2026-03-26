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

export interface Server {
  name: string;
  template: string;
  status: string;
  url: string;
  description: string;
  primitives: Primitive[];
  pip_packages: string[];
  env_vars: EnvVar[];
  owner_id: string | null;
  owner_email: string | null;
  created_at: string | null;
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
}

const BASE = "/api";

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
    localStorage.removeItem("token");
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  listTemplates: () => request<Template[]>("/templates"),
  getTemplate: (name: string) => request<Template>(`/templates/${name}`),
  listServers: () => request<Server[]>("/servers"),
  getServer: (name: string) => request<Server>(`/servers/${name}`),
  createServer: (data: CreateServerRequest) =>
    request<Server>("/servers", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  startServer: (name: string) =>
    request<Server>(`/servers/${name}/start`, { method: "POST" }),
  stopServer: (name: string) =>
    request<Server>(`/servers/${name}/stop`, { method: "POST" }),
  deleteServer: (name: string) =>
    request<void>(`/servers/${name}`, { method: "DELETE" }),

  updateDescription: (serverName: string, description: string) =>
    request<Server>(`/servers/${serverName}/description`, {
      method: "PUT",
      body: JSON.stringify({ description }),
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
  deployConfig: (serverName: string, pip_packages: string[], env_vars: EnvVar[]) =>
    request<Server>(`/servers/${serverName}/config`, {
      method: "PUT",
      body: JSON.stringify({ pip_packages, env_vars }),
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
