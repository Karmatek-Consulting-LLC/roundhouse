import { useCallback, useEffect, useState } from "react";
import { api, type LogRetentionContext } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2, Trash2 } from "lucide-react";

const CONTEXT_LABELS: Record<string, string> = {
  auth: "Authentication",
  deploy: "Deployments",
  scan: "Registry scans",
  backup: "Backup & restore",
  admin: "Administration",
  system: "System",
};

function formatOldest(iso: string | null): string {
  if (!iso) return "—";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "today" : `${days}d ago`;
}

/** Per-context retention windows + storage stats, with a manual prune. */
export function LogRetentionDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [rows, setRows] = useState<LogRetentionContext[] | null>(null);
  const [defaultDays, setDefaultDays] = useState<number>(90);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null); // context being saved, or "prune"
  const [error, setError] = useState<string | null>(null);
  const [pruned, setPruned] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.getLogRetention();
      setRows(res.contexts);
      setDefaultDays(res.default_days);
      setDrafts(Object.fromEntries(res.contexts.map((c) => [c.context, String(c.days)])));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load retention settings");
    }
  }, []);

  useEffect(() => {
    if (open) {
      setPruned(null);
      void load();
    }
  }, [open, load]);

  async function save(context: string) {
    const days = Number(drafts[context]);
    if (!Number.isFinite(days) || days < 0) return;
    setBusy(context);
    setError(null);
    try {
      const res = await api.putLogRetention(context, Math.floor(days));
      setRows(res.contexts);
      setDrafts(Object.fromEntries(res.contexts.map((c) => [c.context, String(c.days)])));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setBusy(null);
    }
  }

  async function prune() {
    setBusy("prune");
    setError(null);
    try {
      const res = await api.pruneLogsNow();
      setRows(res.contexts);
      const total = Object.values(res.removed).reduce((a, b) => a + b, 0);
      setPruned(
        total === 0
          ? "Nothing to prune — all events are within their windows."
          : `Pruned ${total} event${total === 1 ? "" : "s"} (${Object.entries(res.removed)
              .map(([c, n]) => `${c}: ${n}`)
              .join(", ")}).`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Prune failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Log retention</DialogTitle>
          <DialogDescription>
            Events older than a context's window are pruned hourly. 0 keeps
            events forever. Deployment default: {defaultDays} days.
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}
        {pruned && <div className="text-sm text-muted-foreground">{pruned}</div>}

        {rows === null ? (
          <div className="py-6 text-center text-muted-foreground">Loading…</div>
        ) : (
          <div className="rounded-md border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left">Context</th>
                  <th className="px-3 py-2 text-right">Events</th>
                  <th className="px-3 py-2 text-right">Oldest</th>
                  <th className="px-3 py-2 text-left">Keep (days)</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.context} className="border-t">
                    <td className="px-3 py-2">
                      {CONTEXT_LABELS[c.context] ?? c.context}
                      {!c.custom && (
                        <span className="ml-2 text-xs text-muted-foreground">(default)</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">{c.count}</td>
                    <td className="px-3 py-2 text-right text-xs text-muted-foreground">
                      {formatOldest(c.oldest_ts)}
                    </td>
                    <td className="px-3 py-2">
                      <Input
                        type="number"
                        min={0}
                        value={drafts[c.context] ?? ""}
                        onChange={(e) =>
                          setDrafts((d) => ({ ...d, [c.context]: e.target.value }))
                        }
                        className="h-8 w-24"
                      />
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy !== null || String(c.days) === (drafts[c.context] ?? "")}
                        onClick={() => void save(c.context)}
                      >
                        {busy === c.context ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          "Save"
                        )}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex justify-end">
          <Button
            size="sm"
            variant="destructive"
            disabled={busy !== null}
            onClick={() => void prune()}
          >
            {busy === "prune" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Trash2 className="mr-1 h-3 w-3" />
            )}
            Prune now
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
