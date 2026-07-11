import { useEffect, useState } from "react";
import { api, type VulnSummary } from "@/lib/api";
import { ShieldAlert, ShieldCheck, ShieldQuestion, ExternalLink } from "lucide-react";

/** One-line human summary for tooltips. */
function describe(v: VulnSummary): string {
  if (v.status === "vulnerable") {
    const parts = Object.entries(v.by_severity).map(([s, n]) => `${n} ${s.toLowerCase()}`);
    return `${v.total} vulnerabilities (${parts.join(", ")})${v.fixable ? ` — ${v.fixable} fixable` : ""}`;
  }
  if (v.status === "clean") return "No known vulnerabilities";
  if (v.status === "scanning") return "Scan in progress";
  if (v.status === "unscanned") return v.detail ?? "Not scanned yet";
  return v.detail ?? "Scan status unavailable";
}

function tone(v: VulnSummary): { cls: string; Icon: typeof ShieldAlert } {
  if (v.status === "vulnerable") {
    const critical = (v.by_severity["Critical"] ?? 0) > 0 || v.severity === "Critical";
    return {
      cls: critical
        ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
        : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
      Icon: ShieldAlert,
    };
  }
  if (v.status === "clean") {
    return {
      cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
      Icon: ShieldCheck,
    };
  }
  return { cls: "border-border bg-muted/40 text-muted-foreground", Icon: ShieldQuestion };
}

/** Compact per-row badge for the servers list. */
export function VulnBadge({ summary }: { summary: VulnSummary }) {
  const { cls, Icon } = tone(summary);
  const label =
    summary.status === "vulnerable"
      ? String(summary.total)
      : summary.status === "clean"
        ? "clean"
        : summary.status === "scanning"
          ? "scanning"
          : "—";
  const badge = (
    <span
      title={describe(summary)}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px] ${cls}`}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
  return summary.report_url ? (
    <a href={summary.report_url} target="_blank" rel="noopener noreferrer">
      {badge}
    </a>
  ) : (
    badge
  );
}

/** Self-fetching hook shared by the list (bulk) and the editor panel. */
export function useVulnerabilities(deps: unknown[] = []) {
  const [vulns, setVulns] = useState<Record<string, VulnSummary> | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .getVulnerabilities()
      .then((d) => {
        if (!cancelled) setVulns(d.available ? d.servers : null);
      })
      .catch(() => {
        if (!cancelled) setVulns(null);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return vulns;
}

/** Overview-tab panel: severity breakdown + scan time + registry deep link.
 * Renders nothing when no scanner is configured or the server has no data. */
export function VulnPanel({ serverName }: { serverName: string }) {
  const vulns = useVulnerabilities([serverName]);
  const v = vulns?.[serverName];
  if (!v || v.status === "unsupported") return null;
  const { cls, Icon } = tone(v);

  return (
    <div className="rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${cls}`}>
          <Icon className="h-3.5 w-3.5" />
          {v.status === "vulnerable"
            ? `${v.total} image vulnerabilities`
            : v.status === "clean"
              ? "Image scan clean"
              : v.status === "scanning"
                ? "Image scan running"
                : v.status === "unscanned"
                  ? "Image not scanned"
                  : "Image scan unavailable"}
        </span>
        {v.status === "vulnerable" &&
          Object.entries(v.by_severity).map(([sev, n]) => (
            <span key={sev} className="font-mono text-xs text-muted-foreground">
              {sev}: <span className="font-semibold text-foreground">{n}</span>
            </span>
          ))}
        {v.status === "vulnerable" && v.fixable > 0 && (
          <span className="font-mono text-xs text-muted-foreground">
            fixable: <span className="font-semibold text-foreground">{v.fixable}</span>
          </span>
        )}
        <span className="ml-auto flex items-center gap-3">
          {v.scanned_at && (
            <span className="text-xs text-muted-foreground">
              scanned {new Date(v.scanned_at).toLocaleString()}
            </span>
          )}
          {v.report_url && (
            <a
              href={v.report_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              View in registry <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </span>
      </div>
      {(v.status === "error" || v.status === "unscanned") && v.detail && (
        <p className="mt-2 text-xs text-muted-foreground">{v.detail}</p>
      )}
    </div>
  );
}
