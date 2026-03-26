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

export interface Server {
  name: string;
  template: string;
  status: string;
  url: string;
  created_at: string | null;
}

export interface CreateServerRequest {
  name: string;
  template: string;
  config: Record<string, string>;
}

const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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
};
