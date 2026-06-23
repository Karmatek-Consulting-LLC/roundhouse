import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EnvVarsEditor } from "@/components/env-vars-editor";
import { SsoSettingsCard } from "@/components/sso-settings";
import type { EnvVar } from "@/lib/api";
import { ArrowLeft, Boxes, Container, Globe, KeyRound, Save, Shield, Trash2, Variable } from "lucide-react";

interface PlatformSettingsProps {
  onBack: () => void;
}

export function PlatformSettings({ onBack }: PlatformSettingsProps) {
  const [baseUrl, setBaseUrl] = useState("");
  const [defaultReplicas, setDefaultReplicas] = useState<number | null>(null);
  const [maxReplicas, setMaxReplicas] = useState<number | null>(null);
  const [swarmMode, setSwarmMode] = useState<boolean | null>(null);
  const [dockerRegistry, setDockerRegistry] = useState("");
  const [savedDockerRegistry, setSavedDockerRegistry] = useState("");
  const [dockerRegistryEffective, setDockerRegistryEffective] = useState("");
  const [dockerUsername, setDockerUsername] = useState("");
  const [savedDockerUsername, setSavedDockerUsername] = useState("");
  const [registryPassword, setRegistryPassword] = useState("");
  const [passwordClearRequested, setPasswordClearRequested] = useState(false);
  const [registryPasswordConfigured, setRegistryPasswordConfigured] = useState(false);
  const [customCaConfigured, setCustomCaConfigured] = useState(false);
  const [customCaCount, setCustomCaCount] = useState(0);
  const [customCaInput, setCustomCaInput] = useState("");
  const [savingCustomCa, setSavingCustomCa] = useState(false);
  const [customCaError, setCustomCaError] = useState<string | null>(null);
  const [globalEnvVars, setGlobalEnvVars] = useState<EnvVar[]>([]);
  const [savedGlobalEnvVars, setSavedGlobalEnvVars] = useState<EnvVar[]>([]);
  const [savingGlobalEnv, setSavingGlobalEnv] = useState(false);
  const [loading, setLoading] = useState(true);
  const [savingRegistry, setSavingRegistry] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getSettings();
      setBaseUrl(data.base_url);
      setDefaultReplicas(data.default_mcp_server_replicas);
      setMaxReplicas(data.max_mcp_server_replicas);
      setSwarmMode(data.docker_swarm_mode);
      setDockerRegistry(data.docker_registry);
      setSavedDockerRegistry(data.docker_registry);
      setDockerRegistryEffective(data.docker_registry_effective);
      setDockerUsername(data.docker_registry_username);
      setSavedDockerUsername(data.docker_registry_username);
      setRegistryPasswordConfigured(data.docker_registry_password_configured);
      setRegistryPassword("");
      setPasswordClearRequested(false);
      setCustomCaConfigured(data.custom_ca_cert_configured);
      setCustomCaCount(data.custom_ca_cert_count ?? 0);
      try {
        const envData = await api.getMcpEnvSettings();
        setGlobalEnvVars(envData.env_vars);
        setSavedGlobalEnvVars(envData.env_vars);
      } catch {
        setGlobalEnvVars([]);
        setSavedGlobalEnvVars([]);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleSaveDockerRegistry() {
    setSavingRegistry(true);
    setError(null);
    try {
      const body: { registry: string; username: string; password?: string } = {
        registry: dockerRegistry,
        username: dockerUsername,
      };
      if (registryPassword.length > 0) {
        body.password = registryPassword;
      } else if (passwordClearRequested) {
        body.password = "";
      }
      const data = await api.updateDockerRegistry(body);
      setSavedDockerRegistry(data.docker_registry);
      setSavedDockerUsername(data.docker_registry_username);
      setDockerRegistryEffective(data.docker_registry_effective);
      setRegistryPasswordConfigured(data.docker_registry_password_configured);
      setRegistryPassword("");
      setPasswordClearRequested(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save registry");
    } finally {
      setSavingRegistry(false);
    }
  }

  const dockerRegistryDirty =
    dockerRegistry !== savedDockerRegistry ||
    dockerUsername !== savedDockerUsername ||
    registryPassword.length > 0 ||
    passwordClearRequested;

  const globalEnvDirty =
    JSON.stringify(globalEnvVars) !== JSON.stringify(savedGlobalEnvVars);

  async function handleSaveGlobalEnv() {
    setSavingGlobalEnv(true);
    setError(null);
    try {
      const filtered = globalEnvVars.filter((v) => v.name.trim());
      const data = await api.putMcpEnvSettings(filtered);
      setGlobalEnvVars(data.env_vars);
      setSavedGlobalEnvVars(data.env_vars);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save global environment variables");
    } finally {
      setSavingGlobalEnv(false);
    }
  }

  async function handleSaveCustomCa() {
    setCustomCaError(null);
    setSavingCustomCa(true);
    try {
      const r = await api.updateCustomCa(customCaInput);
      setCustomCaConfigured(r.custom_ca_cert_configured);
      setCustomCaCount(r.cert_count ?? 0);
      setCustomCaInput("");
    } catch (e) {
      setCustomCaError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSavingCustomCa(false);
    }
  }

  async function handleDeleteCustomCa() {
    if (!confirm("Remove the platform CA bundle? Subsequent server builds won't trust it.")) {
      return;
    }
    setCustomCaError(null);
    try {
      const r = await api.deleteCustomCa();
      setCustomCaConfigured(r.custom_ca_cert_configured);
      setCustomCaCount(0);
    } catch (e) {
      setCustomCaError(e instanceof Error ? e.message : "Failed to delete");
    }
  }

  if (loading) {
    return <div className="py-12 text-center text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back
        </Button>
      </div>

      <h2 className="text-2xl font-semibold tracking-tight">Platform Settings</h2>

      <SsoSettingsCard />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Container className="h-5 w-5" />
            Docker image registry
          </CardTitle>
          <CardDescription>
            Images are tagged{" "}
            <code className="rounded bg-muted px-1 text-xs break-all">
              PREFIX/mcp-server-&lt;server&gt;:latest
            </code>
            . For Harbor, set PREFIX to <code className="text-xs">registry-host/project</code>. Leave PREFIX
            empty to keep images on the build host only.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 sm:items-start">
            <div className="grid gap-2 min-w-0">
              <Label htmlFor="docker-registry">Registry prefix</Label>
              <Input
                id="docker-registry"
                placeholder="registry.host/va-project-name"
                value={dockerRegistry}
                onChange={(e) => setDockerRegistry(e.target.value)}
                className="font-mono text-sm"
              />
              {dockerRegistry.length > 0 && !dockerRegistry.includes("/") && (
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  Harbor needs a project in the path:{" "}
                  <code className="break-all">{dockerRegistry}/your-project</code>
                </p>
              )}
            </div>
            <div className="grid gap-2 min-w-0">
              <Label htmlFor="docker-registry-user">Push username</Label>
              <Input
                id="docker-registry-user"
                placeholder="robot$project+push or user account"
                value={dockerUsername}
                onChange={(e) => setDockerUsername(e.target.value)}
                className="font-mono text-sm"
                autoComplete="off"
              />
            </div>
          </div>
          <div className="grid gap-2 min-w-0">
            <Label htmlFor="docker-registry-pass">Push password / token</Label>
            <Input
              id="docker-registry-pass"
              type="password"
              placeholder={
                registryPasswordConfigured
                  ? "Leave blank to keep saved password"
                  : "Harbor robot secret or CLI token"
              }
              value={registryPassword}
              onChange={(e) => {
                setRegistryPassword(e.target.value);
                if (e.target.value.length > 0) setPasswordClearRequested(false);
              }}
              className="font-mono text-sm"
              autoComplete="new-password"
            />
            {registryPasswordConfigured && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto py-1 px-0 text-xs text-muted-foreground hover:text-destructive"
                onClick={() => {
                  setPasswordClearRequested(true);
                  setRegistryPassword("");
                }}
              >
                Clear saved password
              </Button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={handleSaveDockerRegistry}
              disabled={savingRegistry || !dockerRegistryDirty}
              size="sm"
            >
              <Save className="mr-1 h-4 w-4" />
              {savingRegistry ? "Saving…" : "Save"}
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Effective tag prefix:{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono break-all">
              {dockerRegistryEffective || "(none - local images only)"}
            </code>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Boxes className="h-5 w-5" />
            MCP server replicas (Docker)
          </CardTitle>
          <CardDescription>
            Defaults come from environment variables on the platform container (
            <code className="rounded bg-muted px-1">DEFAULT_MCP_SERVER_REPLICAS</code>,{" "}
            <code className="rounded bg-muted px-1">MAX_MCP_SERVER_REPLICAS</code>
            ). Per-server overrides are configured on each server&apos;s detail page.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex flex-wrap gap-4">
            <Badge variant={swarmMode ? "default" : "secondary"}>
              {swarmMode ? "Swarm mode" : "Stand-alone Docker"}
            </Badge>
          </div>
          <p className="text-muted-foreground">
            Default replicas:{" "}
            <strong className="text-foreground">{defaultReplicas ?? "-"}</strong>
            {" · "}
            Max allowed:{" "}
            <strong className="text-foreground">{maxReplicas ?? "-"}</strong>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Variable className="h-5 w-5" />
            Global MCP environment variables
          </CardTitle>
          <CardDescription>
            Applied only to MCP servers that import each name here. Per-server local variables override these
            when the same name is set.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <EnvVarsEditor
            title="Variables"
            hint="No global variables. Add keys shared by all servers (for example API endpoint URLs)."
            envVars={globalEnvVars}
            onChange={setGlobalEnvVars}
          />
          <Button
            type="button"
            size="sm"
            onClick={() => void handleSaveGlobalEnv()}
            disabled={savingGlobalEnv || !globalEnvDirty}
          >
            <Save className="mr-1 h-4 w-4" />
            {savingGlobalEnv ? "Saving…" : "Save global env"}
          </Button>
        </CardContent>
      </Card>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Globe className="h-5 w-5" />
            Public URL
          </CardTitle>
          <CardDescription>
            The public base URL the platform reports for MCP server URLs and the
            SSO redirect. It's set at deploy time from{" "}
            <code className="rounded bg-muted px-1">PUBLIC_HOSTNAME</code> (the same
            value your ingress routes on), so it stays in sync with routing — and
            is therefore read-only here. To change it, redeploy with a new{" "}
            <code className="rounded bg-muted px-1">PUBLIC_HOSTNAME</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>Current base URL:</span>
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
              {baseUrl}
            </code>
          </div>
          {baseUrl.includes("localhost") && (
            <div className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
              The base URL is still localhost. Set{" "}
              <code className="rounded bg-muted px-1">PUBLIC_HOSTNAME</code> at
              deploy before sharing MCP server URLs or enabling SSO.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="h-5 w-5" />
            Custom CA bundle
          </CardTitle>
          <CardDescription>
            Trusted by the platform itself for outbound calls (e.g. remote-server
            discovery) and baked into every spawned MCP server image so apt-get, pip, and
            the server's own HTTPS calls trust your CA(s). Paste a PEM bundle — one or more{" "}
            <code className="rounded bg-muted px-1">-----BEGIN CERTIFICATE-----</code> blocks.
            Add a CA <strong>per upstream</strong>, each with its <strong>full chain</strong>{" "}
            (root + intermediates) — "unable to get local issuer" usually means a missing
            intermediate. Saving <strong>replaces</strong> the whole bundle, so paste all CAs
            together. Server-image trust takes effect on the next rebuild.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Badge variant={customCaConfigured ? "default" : "secondary"}>
              <Shield className="mr-1 h-3 w-3" />
              {customCaConfigured
                ? `${customCaCount} certificate${customCaCount === 1 ? "" : "s"} trusted`
                : "No custom CA"}
            </Badge>
          </div>

          <div className="grid gap-2">
            <Label>PEM bundle</Label>
            <Textarea
              className="min-h-[160px] font-mono text-xs"
              placeholder={"-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----"}
              value={customCaInput}
              onChange={(e) => setCustomCaInput(e.target.value)}
            />
          </div>

          {customCaError && <p className="text-sm text-destructive">{customCaError}</p>}

          <div className="flex items-center gap-3">
            <Button
              onClick={handleSaveCustomCa}
              disabled={savingCustomCa || !customCaInput.trim()}
              size="sm"
            >
              <Save className="mr-1 h-4 w-4" />
              {savingCustomCa ? "Saving..." : customCaConfigured ? "Replace CA bundle" : "Save CA bundle"}
            </Button>
            {customCaConfigured && (
              <Button variant="destructive" size="sm" onClick={handleDeleteCustomCa}>
                <Trash2 className="mr-1 h-4 w-4" />
                Remove CA bundle
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
