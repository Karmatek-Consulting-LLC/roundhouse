import { useMemo } from "react";
import { Activity, AlertTriangle, Gauge, Server as ServerIcon } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ObsTimeseries } from "@/lib/api";
import { fmtMs, fmtNum, fmtPct } from "./format";

function Sparkline({
  values,
  color,
  height = 36,
}: {
  values: number[];
  color: string;
  height?: number;
}) {
  const width = 132;
  const gid = useMemo(() => "spark-" + Math.random().toString(36).slice(2), []);
  if (values.length === 0) {
    return <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" />;
  }
  const max = Math.max(1, ...values);
  const step = values.length > 1 ? width / (values.length - 1) : width;
  const pts = values.map(
    (v, i) => `${(i * step).toFixed(1)},${(height - (v / max) * (height - 3) - 1).toFixed(1)}`,
  );
  const line = pts.join(" ");
  const area = `0,${height} ${line} ${((values.length - 1) * step).toFixed(1)},${height}`;
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.35} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${gid})`} />
      <polyline
        points={line}
        fill="none"
        stroke={color}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function Tile({
  label,
  value,
  sub,
  icon,
  spark,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ReactNode;
  spark?: React.ReactNode;
  tone?: "default" | "danger";
}) {
  return (
    <Card className="relative overflow-hidden p-4">
      <div className="flex items-start justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <span className={cn("text-muted-foreground", tone === "danger" && "text-destructive")}>
          {icon}
        </span>
      </div>
      <div
        className={cn(
          "mt-1 font-display text-3xl font-extrabold tabular-nums tracking-tight",
          tone === "danger" && "text-destructive",
        )}
      >
        {value}
      </div>
      {sub && <div className="font-mono text-[11px] text-muted-foreground">{sub}</div>}
      {spark && <div className="mt-2 -mx-1">{spark}</div>}
    </Card>
  );
}

export function KpiTiles({
  data,
  rangeSeconds,
  runningServers,
  totalServers,
}: {
  data: ObsTimeseries | null;
  rangeSeconds: number;
  runningServers: number;
  totalServers: number;
}) {
  const m = useMemo(() => {
    const buckets = data?.buckets ?? [];
    const totalCalls = buckets.reduce((a, b) => a + b.calls, 0);
    const totalErrors = buckets.reduce((a, b) => a + b.errors, 0);
    const perMin = rangeSeconds > 0 ? (totalCalls / rangeSeconds) * 60 : 0;
    const errorRate = totalCalls > 0 ? (totalErrors / totalCalls) * 100 : 0;
    let currentP95: number | null = null;
    for (let i = buckets.length - 1; i >= 0; i--) {
      if (buckets[i].p95_ms != null) {
        currentP95 = buckets[i].p95_ms;
        break;
      }
    }
    return {
      perMin,
      errorRate,
      currentP95,
      callsSpark: buckets.map((b) => b.calls),
      errSpark: buckets.map((b) => (b.calls > 0 ? (b.errors / b.calls) * 100 : 0)),
      p95Spark: buckets.map((b) => b.p95_ms ?? 0),
    };
  }, [data, rangeSeconds]);

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Tile
        label="Req / min"
        value={fmtNum(m.perMin)}
        sub="across window"
        icon={<Activity className="h-4 w-4" />}
        spark={<Sparkline values={m.callsSpark} color="var(--primary)" />}
      />
      <Tile
        label="Error rate"
        value={fmtPct(m.errorRate)}
        sub="errors / calls"
        tone={m.errorRate >= 1 ? "danger" : "default"}
        icon={<AlertTriangle className="h-4 w-4" />}
        spark={<Sparkline values={m.errSpark} color="var(--destructive)" />}
      />
      <Tile
        label="p95 latency"
        value={fmtMs(m.currentP95)}
        sub="current bucket"
        icon={<Gauge className="h-4 w-4" />}
        spark={<Sparkline values={m.p95Spark} color="var(--chart-3)" />}
      />
      <Tile
        label="Active servers"
        value={`${runningServers}/${totalServers}`}
        sub="running / total"
        icon={<ServerIcon className="h-4 w-4" />}
      />
    </div>
  );
}
