import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type DeploymentInfo,
  type RestorePreview,
  type RestoreResult,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Database,
  Download,
  Loader2,
  RotateCcw,
  Upload,
} from "lucide-react";

interface BackupRestoreProps {
  onBack: () => void;
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function CountRow({ counts }: { counts: { servers: number; users: number; server_tokens: number } }) {
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1 font-mono text-sm">
      <span>{counts.servers} servers</span>
      <span>{counts.users} users</span>
      <span>{counts.server_tokens} tokens</span>
    </div>
  );
}

export function BackupRestore({ onBack }: BackupRestoreProps) {
  const [info, setInfo] = useState<DeploymentInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [force, setForce] = useState(false);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [result, setResult] = useState<RestoreResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadInfo = useCallback(async () => {
    setInfoError(null);
    try {
      setInfo(await api.getBackupInfo());
    } catch (e) {
      setInfoError(e instanceof Error ? e.message : "Failed to load deployment info");
    }
  }, []);

  useEffect(() => {
    void loadInfo();
  }, [loadInfo]);

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      const { blob, filename } = await api.exportBackup();
      triggerDownload(blob, filename);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Backup failed");
    } finally {
      setExporting(false);
    }
  }

  async function handleFile(picked: File | null) {
    setFile(picked);
    setPreview(null);
    setPreviewError(null);
    setResult(null);
    setForce(false);
    if (!picked) return;
    setPreviewing(true);
    try {
      setPreview(await api.previewRestore(picked));
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : "Could not read backup");
    } finally {
      setPreviewing(false);
    }
  }

  async function handleRestore() {
    if (!file) return;
    setConfirmOpen(false);
    setRestoring(true);
    setRestoreError(null);
    setResult(null);
    try {
      const res = await api.restoreBackup(file, force);
      setResult(res);
      await loadInfo();
    } catch (e) {
      setRestoreError(e instanceof Error ? e.message : "Restore failed");
    } finally {
      setRestoring(false);
    }
  }

  const problems = preview?.problems ?? [];
  const hasBlockingProblems = problems.length > 0;
  const canRestore = !!file && !previewing && !restoring && (!hasBlockingProblems || force);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back
        </Button>
        <h1 className="text-2xl font-semibold">Backup &amp; Restore</h1>
      </div>

      <p className="text-sm text-muted-foreground">
        A backup is a single file containing the entire deployment — users, teams,
        servers, tokens, assets, and settings. Restoring replaces this deployment
        with the backup&apos;s contents and rebuilds every MCP server to match.
      </p>

      {/* Current deployment */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-5 w-5" />
            This deployment
          </CardTitle>
          <CardDescription>What a backup taken now would contain.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {infoError && <ErrorNote>{infoError}</ErrorNote>}
          {!info && !infoError && <Muted>Loading…</Muted>}
          {info && (
            <>
              {!info.postgres && (
                <ErrorNote>
                  Backup &amp; restore require the Postgres backend. This deployment is
                  not running on Postgres, so these actions are unavailable.
                </ErrorNote>
              )}
              <CountRow counts={info.counts} />
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                <Meta label="Public URL" value={info.base_url} />
                <Meta label="Orchestrator" value={info.orchestrator} />
                <Meta label="Schema rev" value={info.alembic_revision ?? "—"} mono />
                <Meta label="APP_KEY id" value={info.app_key_fingerprint ?? "(none set)"} mono />
              </dl>
            </>
          )}
        </CardContent>
      </Card>

      {/* Export */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Download className="h-5 w-5" />
            Download a backup
          </CardTitle>
          <CardDescription>
            Saves a <code>.tar.gz</code> to your machine. Store it somewhere safe —
            it contains encrypted secrets and is only restorable on a deployment
            with the same <code>APP_KEY</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {exportError && <ErrorNote>{exportError}</ErrorNote>}
          <Button onClick={handleExport} disabled={exporting || !info?.postgres}>
            {exporting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
            {exporting ? "Preparing…" : "Download backup"}
          </Button>
        </CardContent>
      </Card>

      {/* Restore */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5" />
            Restore from a backup
          </CardTitle>
          <CardDescription>
            Replaces this deployment&apos;s data, then rebuilds servers to match.
            Existing servers not in the backup are torn down.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <input
            ref={fileInputRef}
            type="file"
            accept=".gz,.tar.gz,application/gzip"
            className="block w-full text-sm file:mr-4 file:rounded-md file:border-0 file:bg-secondary file:px-4 file:py-2 file:text-sm file:font-medium hover:file:bg-secondary/80"
            onChange={(e) => void handleFile(e.target.files?.[0] ?? null)}
            disabled={!info?.postgres || restoring}
          />

          {previewing && <Muted><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Reading backup…</Muted>}
          {previewError && <ErrorNote>{previewError}</ErrorNote>}

          {preview && (
            <div className="space-y-3 rounded-md border p-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">Backup contents</span>
                <Badge variant="secondary">
                  {new Date(preview.manifest.created_at).toLocaleString()}
                </Badge>
              </div>
              <CountRow counts={preview.manifest.counts} />
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                <Meta label="From URL" value={preview.manifest.base_url} />
                <Meta label="Schema rev" value={preview.manifest.alembic_revision ?? "—"} mono />
                <Meta
                  label="APP_KEY id"
                  value={preview.manifest.app_key_fingerprint ?? "(none)"}
                  mono
                />
              </dl>

              {problems.length > 0 ? (
                <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-destructive">
                    <AlertTriangle className="h-4 w-4" />
                    This backup is not safe to restore here
                  </div>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-destructive">
                    {problems.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                  <label className="flex items-start gap-2 pt-1 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={force}
                      onChange={(e) => setForce(e.target.checked)}
                    />
                    <span>
                      I understand the risks (tokens may be undecryptable, or the schema
                      may not match) and want to restore anyway.
                    </span>
                  </label>
                </div>
              ) : (
                <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="h-4 w-4" />
                  Validated — safe to restore on this deployment.
                </div>
              )}
            </div>
          )}

          {restoreError && <ErrorNote>{restoreError}</ErrorNote>}

          {result && (
            <div className="space-y-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-4">
              <div className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4" />
                Restore complete
              </div>
              <div className="font-mono text-sm">
                {result.reconcile.redeployed.length} redeployed · {result.reconcile.reaped.length} reaped ·{" "}
                {result.reconcile.errors.length} errors
              </div>
              {result.reconcile.errors.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 text-sm text-destructive">
                  {result.reconcile.errors.map((er, i) => (
                    <li key={i}>
                      <span className="font-mono">{er.server}</span> ({er.op}): {er.error}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <Button
            variant="destructive"
            disabled={!canRestore}
            onClick={() => setConfirmOpen(true)}
          >
            {restoring ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
            {restoring ? "Restoring — this can take several minutes…" : "Restore from this backup"}
          </Button>
        </CardContent>
      </Card>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              Replace this deployment?
            </DialogTitle>
            <DialogDescription>
              This wipes the current database and replaces it with the backup, then
              rebuilds every server. Servers running now but not in the backup are
              destroyed. This cannot be undone — take a fresh backup first if unsure.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void handleRestore()}>
              Restore now
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "font-mono" : ""}>{value}</dd>
    </>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {children}
    </div>
  );
}
