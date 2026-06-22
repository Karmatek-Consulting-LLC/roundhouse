import { useMemo } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { Server } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

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

/** The one fleet/inventory widget kept on the unified console: at-a-glance
 * health of the server fleet (running / stopped / not-deployed). Traffic lives
 * in the timeseries + breakdown panels; this answers "what state is my fleet
 * in", which the traffic store can't. */
function QuickStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "default" | "danger";
}) {
  return (
    <div className="space-y-0.5">
      <div
        className={cn(
          "font-display text-3xl font-extrabold tabular-nums leading-none",
          tone === "danger" && value > 0 && "text-destructive",
        )}
      >
        {value}
      </div>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
    </div>
  );
}

export function FleetStatusCard({ servers }: { servers: Server[] }) {
  const statusData = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of servers) counts[s.status] = (counts[s.status] ?? 0) + 1;
    return Object.entries(counts)
      .map(([status, value]) => ({ status, value }))
      .sort((a, b) => b.value - a.value);
  }, [servers]);

  const stats = useMemo(() => {
    let running = 0;
    let notDeployed = 0;
    let unhealthy = 0;
    for (const s of servers) {
      if (s.status === "running") running += 1;
      if (s.status === "not_deployed") notDeployed += 1;
      if (s.status === "running" && s.health === "unhealthy") unhealthy += 1;
    }
    return { total: servers.length, running, notDeployed, unhealthy };
  }, [servers]);

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-3">
        <CardTitle className="font-display text-sm font-bold uppercase tracking-[0.08em] text-muted-foreground">
          Fleet status
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6 lg:flex-row lg:items-center">
        <div className="flex items-center gap-3">
          <div className="h-[160px] w-[160px] shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={statusData}
                  dataKey="value"
                  nameKey="status"
                  innerRadius={48}
                  outerRadius={74}
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
          <div className="flex flex-col gap-1.5">
            {statusData.map((d) => (
              <div key={d.status} className="flex items-center gap-1.5 text-xs">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-sm"
                  style={{ background: statusColor(d.status) }}
                />
                <span className="text-muted-foreground">{statusLabel(d.status)}</span>
                <span className="font-mono font-medium tabular-nums">{d.value}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="grid flex-1 grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4 lg:border-l lg:border-border lg:pl-8">
          <QuickStat label="Servers" value={stats.total} />
          <QuickStat label="Running" value={stats.running} />
          <QuickStat label="Not deployed" value={stats.notDeployed} />
          <QuickStat label="Unhealthy" value={stats.unhealthy} tone="danger" />
        </div>
      </CardContent>
    </Card>
  );
}
