import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft, Globe, Lock, Save, Shield, Trash2, Upload } from "lucide-react";

interface PlatformSettingsProps {
  onBack: () => void;
}

export function PlatformSettings({ onBack }: PlatformSettingsProps) {
  const [hostname, setHostname] = useState("");
  const [savedHostname, setSavedHostname] = useState("");
  const [tlsEnabled, setTlsEnabled] = useState(false);
  const [hasCert, setHasCert] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const certRef = useRef<HTMLInputElement>(null);
  const keyRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getSettings();
      setHostname(data.hostname);
      setSavedHostname(data.hostname);
      setTlsEnabled(data.tls_enabled);
      setHasCert(data.has_certificate);
      setBaseUrl(data.base_url);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleSaveHostname() {
    setSaving(true);
    setError(null);
    try {
      const data = await api.updateHostname(hostname);
      setSavedHostname(hostname);
      setBaseUrl(data.base_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function handleUploadCert() {
    const certFile = certRef.current?.files?.[0];
    const keyFile = keyRef.current?.files?.[0];
    if (!certFile || !keyFile) {
      setError("Select both certificate and key files");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const data = await api.uploadCertificate(certFile, keyFile);
      setTlsEnabled(data.tls_enabled);
      setHasCert(true);
      setBaseUrl(data.base_url);
      if (certRef.current) certRef.current.value = "";
      if (keyRef.current) keyRef.current.value = "";
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to upload");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteCert() {
    setError(null);
    try {
      const data = await api.deleteCertificate();
      setTlsEnabled(data.tls_enabled);
      setHasCert(false);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete");
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

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Globe className="h-5 w-5" />
            Hostname
          </CardTitle>
          <CardDescription>
            Set the public hostname used in MCP server URLs.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Input
              placeholder="mcp.yourcompany.com"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              className="max-w-md"
            />
            <Button
              onClick={handleSaveHostname}
              disabled={saving || hostname === savedHostname}
              size="sm"
            >
              <Save className="mr-1 h-4 w-4" />
              {saving ? "Saving..." : "Save"}
            </Button>
          </div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>Current base URL:</span>
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
              {baseUrl}
            </code>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Lock className="h-5 w-5" />
            TLS Certificate
          </CardTitle>
          <CardDescription>
            Upload a TLS certificate and private key for HTTPS. Traefik picks up changes automatically.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Badge variant={tlsEnabled ? "default" : "secondary"}>
              <Shield className="mr-1 h-3 w-3" />
              {tlsEnabled ? "TLS Enabled" : "TLS Disabled"}
            </Badge>
            {hasCert && (
              <Badge variant="outline">Certificate installed</Badge>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label>Certificate (PEM)</Label>
              <Input ref={certRef} type="file" accept=".pem,.crt,.cer" />
            </div>
            <div className="grid gap-2">
              <Label>Private Key (PEM)</Label>
              <Input ref={keyRef} type="file" accept=".pem,.key" />
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={handleUploadCert} disabled={uploading} size="sm">
              <Upload className="mr-1 h-4 w-4" />
              {uploading ? "Uploading..." : "Upload & Enable TLS"}
            </Button>
            {hasCert && (
              <Button
                variant="destructive"
                size="sm"
                onClick={handleDeleteCert}
              >
                <Trash2 className="mr-1 h-4 w-4" />
                Remove Certificate
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
