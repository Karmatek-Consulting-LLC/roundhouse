import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type AuditEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ArrowLeft, Loader2, RefreshCw } from "lucide-react";

interface AuditLogPageProps {
  onBack: () => void;
}

const TARGET_TYPES = [
  { value: "", label: "All types" },
  { value: "server", label: "Server" },
  { value: "server_token", label: "Server token" },
] as const;

const ACTION_PILL_COLORS: Record<string, string> = {
  "server.create": "bg-green-500/15 text-green-700 dark:text-green-300",
  "server.delete": "bg-red-500/15 text-red-700 dark:text-red-300",
  "server.redeploy": "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  "server.start": "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  "server.stop": "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  "server.import": "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  "server.deploy_from_git": "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
  "token.mint": "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
  "token.revoke": "bg-rose-500/15 text-rose-700 dark:text-rose-300",
};

function actionPill(action: string) {
  const cls = ACTION_PILL_COLORS[action] ?? "bg-muted text-muted-foreground";
  return (
    <span className={`inline-flex rounded px-2 py-0.5 text-xs font-mono ${cls}`}>
      {action}
    </span>
  );
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString();
}

const tooltipStyle = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--popover-foreground)",
  fontSize: 12,
  padding: "6px 10px",
} as const;

// Relocated from the old Dashboard: 14-day rollup of audit activity, derived
// from the loaded events so it always matches the table below.
function ActivityChart({ events }: { events: AuditEvent[] }) {
  const data = useMemo(() => {
    const days = 14;
    const byDate: Record<string, number> = {};
    for (const e of events) {
      if (e.created_at) {
        const d = e.created_at.slice(0, 10);
        byDate[d] = (byDate[d] ?? 0) + 1;
      }
    }
    const out: { label: string; count: number }[] = [];
    const today = new Date();
    for (let i = days - 1; i >= 0; i--) {
      const dt = new Date(today);
      dt.setDate(today.getDate() - i);
      const key = dt.toISOString().slice(0, 10);
      out.push({ label: `${dt.getMonth() + 1}/${dt.getDate()}`, count: byDate[key] ?? 0 });
    }
    return out;
  }, [events]);

  return (
    <div className="rounded-md border p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Activity (last 14 days)
      </h3>
      <div className="h-[160px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
            <XAxis
              dataKey="label"
              tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
              stroke="var(--border)"
              interval={1}
            />
            <YAxis
              tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
              stroke="var(--border)"
              allowDecimals={false}
              width={28}
            />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--muted)", opacity: 0.4 }} />
            <Bar dataKey="count" name="events" fill="var(--chart-1)" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function AuditLogPage({ onBack }: AuditLogPageProps) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [targetType, setTargetType] = useState<string>("");
  const [targetId, setTargetId] = useState<string>("");
  const [limit, setLimit] = useState<number>(100);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listAuditEvents({
        target_type: targetType || undefined,
        target_id: targetId || undefined,
        limit,
      });
      setEvents(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load audit log");
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, [targetType, targetId, limit]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" /> Back
        </Button>
        <h2 className="text-lg font-semibold">Audit Log</h2>
        <span className="text-sm text-muted-foreground">
          {events ? `${events.length} event${events.length === 1 ? "" : "s"}` : ""}
        </span>
        <div className="ml-auto">
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            {loading ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-3 w-3" />
            )}
            Refresh
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-md border p-3 bg-muted/30">
        <div className="grid gap-1">
          <Label className="text-xs">Target type</Label>
          <Select value={targetType || "all"} onValueChange={(v) => setTargetType(v === "all" ? "" : v)}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TARGET_TYPES.map((t) => (
                <SelectItem key={t.value || "all"} value={t.value || "all"}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1">
          <Label className="text-xs">Target ID</Label>
          <Input
            value={targetId}
            placeholder="(any)"
            onChange={(e) => setTargetId(e.target.value)}
            className="w-56"
          />
        </div>
        <div className="grid gap-1">
          <Label className="text-xs">Limit</Label>
          <Input
            type="number"
            value={limit}
            min={1}
            max={500}
            onChange={(e) => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 100)))}
            className="w-24"
          />
        </div>
      </div>

      {events && events.length > 0 && <ActivityChart events={events} />}

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="rounded-md border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left">When</th>
              <th className="px-3 py-2 text-left">Actor</th>
              <th className="px-3 py-2 text-left">Action</th>
              <th className="px-3 py-2 text-left">Target</th>
              <th className="px-3 py-2 text-left">Payload</th>
            </tr>
          </thead>
          <tbody>
            {events?.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-muted-foreground italic">
                  No audit events yet.
                </td>
              </tr>
            )}
            {events?.map((e) => (
              <tr key={e.id} className="border-t hover:bg-muted/30">
                <td className="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">
                  {formatWhen(e.created_at)}
                </td>
                <td className="px-3 py-2 whitespace-nowrap">{e.actor_email ?? "—"}</td>
                <td className="px-3 py-2 whitespace-nowrap">{actionPill(e.action)}</td>
                <td className="px-3 py-2 whitespace-nowrap font-mono text-xs">
                  <span className="text-muted-foreground">{e.target_type}:</span> {e.target_id}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-muted-foreground max-w-[400px] truncate">
                  {e.payload ? JSON.stringify(e.payload) : ""}
                </td>
              </tr>
            ))}
            {events === null && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-muted-foreground">
                  Loading…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
