import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Link } from "react-router-dom";
import { api, type AuditEvent, type DashboardUsage, type Server } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Activity,
  AlertTriangle,
  CircleDot,
  PhoneCall,
  Server as ServerIcon,
} from "lucide-react";

interface DashboardProps {
  servers: Server[];
  loading: boolean;
  isSuperAdmin: boolean;
  onSelectServer: (name: string) => void;
}

// Match the StatusBadge palette so the donut reads the same as the table.
const STATUS_COLOR: Record<string, string> = {
  running: "#10b981", // emerald-500
  stopped: "#a1a1aa", // zinc-400
  exited: "#a1a1aa",
  created: "#f59e0b", // amber-500
  not_deployed: "#f59e0b",
  unknown: "#f43f5e", // rose-500
};
const STATUS_FALLBACK = "#a1a1aa";

const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  stopped: "Stopped",
  exited: "Exited",
  created: "Created",
  not_deployed: "Not deployed",
  unknown: "Unknown",
};

const tooltipStyle = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--popover-foreground)",
  fontSize: 12,
  padding: "6px 10px",
} as const;

function statusColor(status: string): string {
  return STATUS_COLOR[status] ?? STATUS_FALLBACK;
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status.replace(/_/g, " ");
}

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

function StatCard({
  label,
  value,
  sub,
  icon,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ReactNode;
  tone?: "default" | "danger";
}) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between p-5">
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {label}
          </p>
          <p
            className={`text-3xl font-semibold tabular-nums ${
              tone === "danger" ? "text-rose-600 dark:text-rose-400" : ""
            }`}
          >
            {value}
          </p>
          {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
        </div>
        <div className="rounded-lg bg-muted p-2 text-muted-foreground">{icon}</div>
      </CardContent>
    </Card>
  );
}

export function Dashboard({
  servers,
  loading,
  isSuperAdmin,
  onSelectServer,
}: DashboardProps) {
  const [usage, setUsage] = useState<DashboardUsage | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);

  useEffect(() => {
    let alive = true;
    async function pull() {
      try {
        const u = await api.getDashboardUsage();
        if (alive) setUsage(u);
      } catch {
        /* usage is best-effort; leave prior snapshot in place */
      }
    }
    pull();
    const id = setInterval(pull, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!isSuperAdmin) return;
    let alive = true;
    api
      .listAuditEvents({ limit: 300 })
      .then((e) => {
        if (alive) setEvents(e);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [isSuperAdmin]);

  const statusData = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of servers) counts[s.status] = (counts[s.status] ?? 0) + 1;
    return Object.entries(counts)
      .map(([status, value]) => ({ status, value }))
      .sort((a, b) => b.value - a.value);
  }, [servers]);

  const runningCount = useMemo(
    () => servers.filter((s) => s.status === "running").length,
    [servers],
  );
  const notDeployedCount = useMemo(
    () => servers.filter((s) => s.status === "not_deployed").length,
    [servers],
  );

  const ownerData = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of servers) {
      const o = s.owner_email ?? "—";
      counts[o] = (counts[o] ?? 0) + 1;
    }
    return Object.entries(counts)
      .map(([owner, value]) => ({ owner, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);
  }, [servers]);

  const kindData = useMemo(() => {
    const by = usage?.by_kind ?? {};
    return Object.entries(by)
      .map(([kind, calls]) => ({ kind, calls }))
      .sort((a, b) => b.calls - a.calls);
  }, [usage]);
  const kindMax = Math.max(1, ...kindData.map((k) => k.calls));

  const activityData = useMemo(() => {
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

  if (loading) {
    return <div className="py-12 text-center text-muted-foreground">Loading...</div>;
  }

  if (servers.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-12 text-center text-muted-foreground">
        No MCP servers registered yet.{" "}
        <Link to="/servers" className="text-primary hover:underline">
          Create one
        </Link>{" "}
        to start seeing platform stats.
      </div>
    );
  }

  const totalCalls = usage?.total_calls ?? 0;
  const totalErrors = usage?.total_errors ?? 0;
  const errorPct = usage ? (usage.error_rate * 100).toFixed(usage.error_rate < 0.1 ? 2 : 1) : "0";
  const topServers = usage?.top_servers ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Overview</h2>
        <p className="text-sm text-muted-foreground">
          Platform health and live usage across your MCP servers.
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Servers"
          value={String(servers.length)}
          sub={`${ownerData.length} owner${ownerData.length === 1 ? "" : "s"}`}
          icon={<ServerIcon className="h-5 w-5" />}
        />
        <StatCard
          label="Running"
          value={String(runningCount)}
          sub={`${notDeployedCount} not deployed`}
          icon={<CircleDot className="h-5 w-5" />}
        />
        <StatCard
          label="Calls"
          value={totalCalls.toLocaleString()}
          sub={
            usage
              ? `across ${usage.scraped_servers} of ${usage.running_servers} running`
              : "scraping…"
          }
          icon={<PhoneCall className="h-5 w-5" />}
        />
        <StatCard
          label="Error rate"
          value={`${errorPct}%`}
          sub={`${totalErrors.toLocaleString()} error${totalErrors === 1 ? "" : "s"}`}
          icon={<AlertTriangle className="h-5 w-5" />}
          tone={totalErrors > 0 ? "danger" : "default"}
        />
      </div>

      {/* Status donut + top servers bar */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Servers by status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-[220px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={statusData}
                    dataKey="value"
                    nameKey="status"
                    innerRadius={55}
                    outerRadius={85}
                    paddingAngle={2}
                    stroke="var(--card)"
                  >
                    {statusData.map((d) => (
                      <Cell key={d.status} fill={statusColor(d.status)} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(value, _n, item) => [
                      value as number,
                      statusLabel((item?.payload as { status?: string })?.status ?? ""),
                    ]}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-2 flex flex-wrap justify-center gap-x-4 gap-y-1">
              {statusData.map((d) => (
                <div key={d.status} className="flex items-center gap-1.5 text-xs">
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-sm"
                    style={{ background: statusColor(d.status) }}
                  />
                  <span className="text-muted-foreground">{statusLabel(d.status)}</span>
                  <span className="font-medium tabular-nums">{d.value}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Top servers by calls</CardTitle>
          </CardHeader>
          <CardContent>
            {topServers.length === 0 ? (
              <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
                No usage recorded yet.
              </div>
            ) : (
              <div className="h-[220px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={topServers}
                    layout="vertical"
                    margin={{ left: 8, right: 16, top: 4, bottom: 4 }}
                  >
                    <XAxis
                      type="number"
                      tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                      stroke="var(--border)"
                      allowDecimals={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      width={110}
                      tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                      stroke="var(--border)"
                    />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--muted)", opacity: 0.4 }} />
                    <Bar dataKey="calls" fill="var(--chart-1)" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Owners bar + calls by kind */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Servers by owner</CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ height: Math.max(140, ownerData.length * 34) }} className="w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={ownerData}
                  layout="vertical"
                  margin={{ left: 8, right: 16, top: 4, bottom: 4 }}
                >
                  <XAxis
                    type="number"
                    tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                    stroke="var(--border)"
                    allowDecimals={false}
                  />
                  <YAxis
                    type="category"
                    dataKey="owner"
                    width={150}
                    tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                    stroke="var(--border)"
                  />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--muted)", opacity: 0.4 }} />
                  <Bar dataKey="value" name="servers" fill="var(--chart-2)" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Calls by primitive kind</CardTitle>
          </CardHeader>
          <CardContent>
            {kindData.length === 0 ? (
              <div className="flex h-[140px] items-center justify-center text-sm text-muted-foreground">
                No usage recorded yet.
              </div>
            ) : (
              <div className="space-y-3 pt-1">
                {kindData.map((k) => (
                  <div key={k.kind} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="capitalize text-muted-foreground">{k.kind}</span>
                      <span className="font-medium tabular-nums">{k.calls.toLocaleString()}</span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${(k.calls / kindMax) * 100}%`,
                          background: "var(--chart-1)",
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Top servers table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Usage by server</CardTitle>
        </CardHeader>
        <CardContent>
          {topServers.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No usage recorded yet. Metrics appear once running servers handle requests.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Server</TableHead>
                  <TableHead className="text-right">Calls</TableHead>
                  <TableHead className="text-right">Errors</TableHead>
                  <TableHead className="text-right">p95 (ms)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {topServers.map((s) => (
                  <TableRow key={s.name}>
                    <TableCell>
                      <button
                        className="font-medium text-primary hover:underline"
                        onClick={() => onSelectServer(s.name)}
                      >
                        {s.name}
                      </button>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {s.calls.toLocaleString()}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${
                        s.errors > 0 ? "text-rose-600 dark:text-rose-400" : "text-muted-foreground"
                      }`}
                    >
                      {s.errors.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {s.p95_ms ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Activity (superadmin) */}
      {isSuperAdmin && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-sm">Activity (last 14 days)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[200px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={activityData} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
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
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Activity className="h-4 w-4" /> Recent activity
              </CardTitle>
            </CardHeader>
            <CardContent>
              {events.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">No recent events.</p>
              ) : (
                <ul className="space-y-3">
                  {events.slice(0, 8).map((e) => (
                    <li key={e.id} className="flex items-start justify-between gap-2 text-sm">
                      <div className="min-w-0">
                        <span className="font-mono text-xs">{e.action}</span>{" "}
                        <span className="text-muted-foreground">{e.target_id}</span>
                        <div className="truncate text-xs text-muted-foreground">
                          {e.actor_email ?? "system"}
                        </div>
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {relativeTime(e.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
