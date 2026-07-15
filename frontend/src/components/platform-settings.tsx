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
import type { EnvVar, TlsCertStatus } from "@/lib/api";
import { ArrowLeft, Boxes, Container, Globe, KeyRound, Lock, Save, Shield, Trash2, Variable } from "lucide-react";

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
  const [tlsCert, setTlsCert] = useState<TlsCertStatus | null>(null);
  const [tlsCertInput, setTlsCertInput] = useState("");
  const [tlsKeyInput, setTlsKeyInput] = useState("");
  const [savingTls, setSavingTls] = useState(false);
  const [tlsError, setTlsError] = useState<string | null>(null);
  const [globalEnvVars, setGlobalEnvVars] = useState<EnvVar[]>([]);
  const [savedGlobalEnvVars, setSavedGlobalEnvVars] = useState<EnvVar[]>([]);
  const [savingGlobalEnv, setSavingGlobalEnv] = useState(false);
  const [loading, setLoading] = useState(true);
  const [savingRegistry, setSavingRegistry] = useState(false);

  // Registry vulnerability scanning (Harbor/Trivy)
  const [registryScanner, setRegistryScanner] = useState("");
  const [savedRegistryScanner, setSavedRegistryScanner] = useState("");
  const [scannerApiUrl, setScannerApiUrl] = useState("");
  const [savedScannerApiUrl, setSavedScannerApiUrl] = useState("");
  const [savingScanner, setSavingScanner] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Base image registry (pull creds for generated servers' FROM images, e.g. dhi.io)
  const [baseRegistry, setBaseRegistry] = useState("");
  const [savedBaseRegistry, setSavedBaseRegistry] = useState("");
  const [baseUsername, setBaseUsername] = useState("");
  const [savedBaseUsername, setSavedBaseUsername] = useState("");
  const [basePassword, setBasePassword] = useState("");
  const [basePasswordClearRequested, setBasePasswordClearRequested] = useState(false);
  const [basePasswordConfigured, setBasePasswordConfigured] = useState(false);
  const [savingBaseRegistry, setSavingBaseRegistry] = useState(false);

  // Base images for generated MCP servers (build + runtime stages)
  const [baseBuildImage, setBaseBuildImage] = useState("");
  const [savedBaseBuildImage, setSavedBaseBuildImage] = useState("");
  const [baseRuntimeImage, setBaseRuntimeImage] = useState("");
  const [savedBaseRuntimeImage, setSavedBaseRuntimeImage] = useState("");
  const [baseBuildEffective, setBaseBuildEffective] = useState("");
  const [baseRuntimeEffective, setBaseRuntimeEffective] = useState("");
  const [savingBaseImages, setSavingBaseImages] = useState(false);

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
      setRegistryScanner(data.registry_scanner ?? "");
      setSavedRegistryScanner(data.registry_scanner ?? "");
      setScannerApiUrl(data.registry_scanner_api_url ?? "");
      setSavedScannerApiUrl(data.registry_scanner_api_url ?? "");
      setBaseRegistry(data.base_registry ?? "");
      setSavedBaseRegistry(data.base_registry ?? "");
      setBaseUsername(data.base_registry_username ?? "");
      setSavedBaseUsername(data.base_registry_username ?? "");
      setBasePasswordConfigured(data.base_registry_password_configured ?? false);
      setBasePassword("");
      setBasePasswordClearRequested(false);
      setBaseBuildImage(data.mcp_base_build_image ?? "");
      setSavedBaseBuildImage(data.mcp_base_build_image ?? "");
      setBaseRuntimeImage(data.mcp_base_runtime_image ?? "");
      setSavedBaseRuntimeImage(data.mcp_base_runtime_image ?? "");
      setBaseBuildEffective(data.mcp_base_build_image_effective ?? "");
      setBaseRuntimeEffective(data.mcp_base_runtime_image_effective ?? "");
      setCustomCaConfigured(data.custom_ca_cert_configured);
      setCustomCaCount(data.custom_ca_cert_count ?? 0);
      setTlsCert(data.tls_cert ?? null);
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

  async function handleSaveBaseRegistry() {
    setSavingBaseRegistry(true);
    setError(null);
    try {
      const body: { registry: string; username: string; password?: string } = {
        registry: baseRegistry,
        username: baseUsername,
      };
      if (basePassword.length > 0) {
        body.password = basePassword;
      } else if (basePasswordClearRequested) {
        body.password = "";
      }
      const data = await api.updateBaseRegistry(body);
      setSavedBaseRegistry(data.base_registry);
      setSavedBaseUsername(data.base_registry_username);
      setBasePasswordConfigured(data.base_registry_password_configured);
      setBasePassword("");
      setBasePasswordClearRequested(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save base registry");
    } finally {
      setSavingBaseRegistry(false);
    }
  }

  const baseRegistryDirty =
    baseRegistry !== savedBaseRegistry ||
    baseUsername !== savedBaseUsername ||
    basePassword.length > 0 ||
    basePasswordClearRequested;

  async function handleSaveBaseImages() {
    setSavingBaseImages(true);
    setError(null);
    try {
      const data = await api.updateBaseImages({
        build_image: baseBuildImage,
        runtime_image: baseRuntimeImage,
      });
      setSavedBaseBuildImage(data.mcp_base_build_image);
      setSavedBaseRuntimeImage(data.mcp_base_runtime_image);
      setBaseBuildEffective(data.mcp_base_build_image_effective);
      setBaseRuntimeEffective(data.mcp_base_runtime_image_effective);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save base images");
    } finally {
      setSavingBaseImages(false);
    }
  }

  const baseImagesDirty =
    baseBuildImage !== savedBaseBuildImage || baseRuntimeImage !== savedBaseRuntimeImage;

  const scannerDirty =
    registryScanner !== savedRegistryScanner || scannerApiUrl !== savedScannerApiUrl;

  async function handleSaveScanner() {
    setSavingScanner(true);
    setError(null);
    try {
      const data = await api.updateRegistryScanner({
        scanner: registryScanner,
        api_url: scannerApiUrl,
      });
      setSavedRegistryScanner(data.registry_scanner);
      setSavedScannerApiUrl(data.registry_scanner_api_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save scanner settings");
    } finally {
      setSavingScanner(false);
    }
  }

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

  async function handleSaveTls() {
    setTlsError(null);
    setSavingTls(true);
    try {
      const r = await api.updateTlsCert(tlsCertInput, tlsKeyInput);
      setTlsCert(r.tls_cert);
      setTlsCertInput("");
      setTlsKeyInput("");
    } catch (e) {
      setTlsError(e instanceof Error ? e.message : "Failed to save certificate");
    } finally {
      setSavingTls(false);
    }
  }

  async function handleDeleteTls() {
    if (
      !confirm(
        "Remove the HTTPS certificate? Traefik falls back to a self-signed " +
          "certificate, so browsers will warn until you upload a new one.",
      )
    ) {
      return;
    }
    setTlsError(null);
    try {
      const r = await api.deleteTlsCert();
      setTlsCert(r.tls_cert);
    } catch (e) {
      setTlsError(e instanceof Error ? e.message : "Failed to remove certificate");
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

          <div className="space-y-3 border-t pt-4">
            <div>
              <p className="text-sm font-medium">Vulnerability scanning</p>
              <p className="text-xs text-muted-foreground">
                Harbor scans pushed images with Trivy. When enabled, Roundhouse reads the
                scan results over Harbor&apos;s API (using the registry credentials above —
                the robot account needs scan/artifact read permission) and shows a
                vulnerability badge per server.
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={registryScanner === "harbor"}
                onChange={(e) => setRegistryScanner(e.target.checked ? "harbor" : "")}
                disabled={!dockerRegistryEffective}
              />
              Registry is Harbor — surface image vulnerabilities in the UI
            </label>
            {!dockerRegistryEffective && (
              <p className="text-xs text-muted-foreground">
                Configure a registry prefix first — scanning reads results for pushed images.
              </p>
            )}
            {registryScanner === "harbor" && (
              <div className="grid gap-2 min-w-0 max-w-md">
                <Label htmlFor="scanner-api-url">Harbor API URL (optional)</Label>
                <Input
                  id="scanner-api-url"
                  placeholder={`https://${(dockerRegistryEffective || "registry-host").split("/")[0]}/api/v2.0`}
                  value={scannerApiUrl}
                  onChange={(e) => setScannerApiUrl(e.target.value)}
                  className="font-mono text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  Leave blank to derive it from the registry host. Set it only when the
                  API is reached on a different URL than docker pulls.
                </p>
              </div>
            )}
            <Button onClick={handleSaveScanner} disabled={savingScanner || !scannerDirty} size="sm">
              <Save className="mr-1 h-4 w-4" />
              {savingScanner ? "Saving…" : "Save"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="h-5 w-5" />
            Base image registry credentials
          </CardTitle>
          <CardDescription>
            Credentials used to <strong>pull</strong> the base images that generated MCP
            servers are built <code className="text-xs">FROM</code> (e.g. Docker Hardened
            Images at <code className="text-xs">dhi.io</code>). Required when the base image
            registry is private, or builds fail with{" "}
            <code className="text-xs">401 Unauthorized</code>. The password is encrypted at
            rest and delivered to the build daemon only.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 sm:items-start">
            <div className="grid gap-2 min-w-0">
              <Label htmlFor="base-registry">Registry host</Label>
              <Input
                id="base-registry"
                placeholder="dhi.io"
                value={baseRegistry}
                onChange={(e) => setBaseRegistry(e.target.value)}
                className="font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">
                Leave blank to derive it from the configured base images.
              </p>
            </div>
            <div className="grid gap-2 min-w-0">
              <Label htmlFor="base-registry-user">Username</Label>
              <Input
                id="base-registry-user"
                placeholder="Docker ID / org name"
                value={baseUsername}
                onChange={(e) => setBaseUsername(e.target.value)}
                className="font-mono text-sm"
                autoComplete="off"
              />
            </div>
          </div>
          <div className="grid gap-2 min-w-0">
            <Label htmlFor="base-registry-pass">Password / access token</Label>
            <Input
              id="base-registry-pass"
              type="password"
              placeholder={
                basePasswordConfigured
                  ? "Leave blank to keep saved password"
                  : "Personal or organization access token"
              }
              value={basePassword}
              onChange={(e) => {
                setBasePassword(e.target.value);
                if (e.target.value.length > 0) setBasePasswordClearRequested(false);
              }}
              className="font-mono text-sm"
              autoComplete="new-password"
            />
            {basePasswordConfigured && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto py-1 px-0 text-xs text-muted-foreground hover:text-destructive"
                onClick={() => {
                  setBasePasswordClearRequested(true);
                  setBasePassword("");
                }}
              >
                Clear saved password
              </Button>
            )}
          </div>
          <Button
            onClick={handleSaveBaseRegistry}
            disabled={savingBaseRegistry || !baseRegistryDirty}
            size="sm"
          >
            <Save className="mr-1 h-4 w-4" />
            {savingBaseRegistry ? "Saving…" : "Save"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Container className="h-5 w-5" />
            MCP server base images
          </CardTitle>
          <CardDescription>
            Base images for the generated servers&apos; multi-stage build. The{" "}
            <strong>build</strong> image (root; ships pip + apt) compiles dependencies; the{" "}
            <strong>runtime</strong> image (non-root, distroless) runs the server. Leave a
            field blank to use the platform&apos;s environment default.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 min-w-0">
            <Label htmlFor="base-build-image">Build image</Label>
            <Input
              id="base-build-image"
              placeholder={baseBuildEffective || "dhi.io/python:3.14-debian13-dev"}
              value={baseBuildImage}
              onChange={(e) => setBaseBuildImage(e.target.value)}
              className="font-mono text-sm"
            />
          </div>
          <div className="grid gap-2 min-w-0">
            <Label htmlFor="base-runtime-image">Runtime image</Label>
            <Input
              id="base-runtime-image"
              placeholder={baseRuntimeEffective || "dhi.io/python:3.14-debian13"}
              value={baseRuntimeImage}
              onChange={(e) => setBaseRuntimeImage(e.target.value)}
              className="font-mono text-sm"
            />
          </div>
          <Button
            onClick={handleSaveBaseImages}
            disabled={savingBaseImages || !baseImagesDirty}
            size="sm"
          >
            <Save className="mr-1 h-4 w-4" />
            {savingBaseImages ? "Saving…" : "Save"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Effective:{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono break-all">
              {baseBuildEffective || "(unset)"}
            </code>{" "}
            →{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono break-all">
              {baseRuntimeEffective || "(unset)"}
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

      {tlsCert?.supported && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lock className="h-5 w-5" />
              HTTPS certificate
            </CardTitle>
            <CardDescription>
              This deployment terminates TLS on its own ingress — no upstream
              reverse proxy needed. Upload your PEM certificate (leaf +
              intermediates, in chain order) and its unencrypted private key.
              The key is stored encrypted and delivered to the ingress as a
              Swarm secret; replacing it triggers a zero-downtime reload. Until a
              certificate is uploaded, the ingress serves a self-signed one and
              browsers will warn.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant={tlsCert.configured ? "default" : "secondary"}>
                <Shield className="mr-1 h-3 w-3" />
                {tlsCert.configured
                  ? `${tlsCert.subject_cn || "certificate"} installed`
                  : "Self-signed (no certificate uploaded)"}
              </Badge>
              {tlsCert.configured && tlsCert.not_after && (
                <span className="text-sm text-muted-foreground">
                  Expires {new Date(tlsCert.not_after).toLocaleDateString()}
                </span>
              )}
            </div>

            <div className="grid gap-2">
              <Label>Certificate (PEM)</Label>
              <Textarea
                className="min-h-[120px] font-mono text-xs"
                placeholder={"-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----"}
                value={tlsCertInput}
                onChange={(e) => setTlsCertInput(e.target.value)}
              />
            </div>

            <div className="grid gap-2">
              <Label>Private key (PEM)</Label>
              <Textarea
                className="min-h-[120px] font-mono text-xs"
                placeholder={"-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----"}
                value={tlsKeyInput}
                onChange={(e) => setTlsKeyInput(e.target.value)}
              />
            </div>

            {tlsError && <p className="text-sm text-destructive">{tlsError}</p>}

            <div className="flex items-center gap-3">
              <Button
                onClick={handleSaveTls}
                disabled={savingTls || !tlsCertInput.trim() || !tlsKeyInput.trim()}
                size="sm"
              >
                <Save className="mr-1 h-4 w-4" />
                {savingTls
                  ? "Applying..."
                  : tlsCert.configured
                    ? "Replace certificate"
                    : "Install certificate"}
              </Button>
              {tlsCert.configured && (
                <Button variant="destructive" size="sm" onClick={handleDeleteTls}>
                  <Trash2 className="mr-1 h-4 w-4" />
                  Remove certificate
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
