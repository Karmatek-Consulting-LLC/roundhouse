import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ObsTimeseries } from "@/lib/api";
import { fmtAxis, fmtMs, fmtNum, KIND_COLOR } from "./format";

const tooltipStyle = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--popover-foreground)",
  fontSize: 12,
  padding: "6px 10px",
} as const;

const axisProps = {
  stroke: "var(--muted-foreground)",
  tick: { fill: "var(--muted-foreground)", fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: "var(--border)" },
} as const;

const KIND_ORDER = ["tool", "resource", "resource_template", "prompt"] as const;
const KIND_LABEL: Record<string, string> = {
  tool: "Tools",
  resource: "Resources",
  resource_template: "Resource templates",
  prompt: "Prompts",
};

function ChartFrame({
  title,
  right,
  children,
  height = 200,
}: {
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  height?: number;
}) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="font-display text-sm font-bold uppercase tracking-[0.08em] text-muted-foreground">
          {title}
        </CardTitle>
        {right}
      </CardHeader>
      <CardContent className="pt-0">
        <div style={{ height }}>{children}</div>
      </CardContent>
    </Card>
  );
}

function EmptyChart({ height }: { height: number }) {
  return (
    <div
      className="flex items-center justify-center text-xs text-muted-foreground"
      style={{ height }}
    >
      No traffic in this window yet.
    </div>
  );
}

export function TrafficChart({ data, height = 220 }: { data: ObsTimeseries | null; height?: number }) {
  const bucketS = data?.bucket_s ?? 60;
  const rows = useMemo(
    () =>
      (data?.buckets ?? []).map((b) => ({
        label: fmtAxis(b.ts, bucketS),
        tool: b.by_kind.tool,
        resource: b.by_kind.resource,
        resource_template: b.by_kind.resource_template,
        prompt: b.by_kind.prompt,
      })),
    [data, bucketS],
  );
  return (
    <ChartFrame title="Call volume" height={height}>
      {rows.length === 0 ? (
        <EmptyChart height={height} />
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={rows} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" minTickGap={28} {...axisProps} />
            <YAxis allowDecimals={false} width={40} {...axisProps} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--muted)", opacity: 0.3 }} />
            {KIND_ORDER.map((k) => (
              <Area
                key={k}
                type="monotone"
                dataKey={k}
                name={KIND_LABEL[k]}
                stackId="1"
                stroke={KIND_COLOR[k]}
                fill={KIND_COLOR[k]}
                fillOpacity={0.25}
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ChartFrame>
  );
}

export function LatencyChart({ data, height = 200 }: { data: ObsTimeseries | null; height?: number }) {
  const bucketS = data?.bucket_s ?? 60;
  const rows = useMemo(
    () =>
      (data?.buckets ?? []).map((b) => ({
        label: fmtAxis(b.ts, bucketS),
        p50: b.p50_ms,
        p95: b.p95_ms,
        p99: b.p99_ms,
      })),
    [data, bucketS],
  );
  return (
    <ChartFrame title="Latency · p50 / p95 / p99" height={height}>
      {rows.length === 0 ? (
        <EmptyChart height={height} />
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" minTickGap={28} {...axisProps} />
            <YAxis width={44} tickFormatter={(v) => fmtMs(v)} {...axisProps} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v) => fmtMs(Number(v))} />
            <Line type="monotone" dataKey="p50" name="p50" stroke="var(--chart-2)" strokeWidth={1.6} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="p95" name="p95" stroke="var(--chart-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="p99" name="p99" stroke="var(--destructive)" strokeWidth={1.6} strokeDasharray="4 2" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ChartFrame>
  );
}

export function ErrorRateChart({ data, height = 160 }: { data: ObsTimeseries | null; height?: number }) {
  const bucketS = data?.bucket_s ?? 60;
  const rows = useMemo(
    () =>
      (data?.buckets ?? []).map((b) => ({
        label: fmtAxis(b.ts, bucketS),
        rate: b.calls > 0 ? Number(((b.errors / b.calls) * 100).toFixed(2)) : 0,
      })),
    [data, bucketS],
  );
  return (
    <ChartFrame title="Error rate" height={height}>
      {rows.length === 0 ? (
        <EmptyChart height={height} />
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={rows} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <defs>
              <linearGradient id="err-grad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--destructive)" stopOpacity={0.4} />
                <stop offset="100%" stopColor="var(--destructive)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" minTickGap={28} {...axisProps} />
            <YAxis width={40} unit="%" {...axisProps} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v) => `${Number(v)}%`} />
            <Area type="monotone" dataKey="rate" name="Error rate" stroke="var(--destructive)" fill="url(#err-grad)" strokeWidth={1.8} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ChartFrame>
  );
}

export function KindDonut({ data, height = 200 }: { data: ObsTimeseries | null; height?: number }) {
  const slices = useMemo(() => {
    const totals: Record<string, number> = {
      tool: 0,
      resource: 0,
      resource_template: 0,
      prompt: 0,
    };
    for (const b of data?.buckets ?? []) {
      totals.tool += b.by_kind.tool;
      totals.resource += b.by_kind.resource;
      totals.resource_template += b.by_kind.resource_template;
      totals.prompt += b.by_kind.prompt;
    }
    return KIND_ORDER.map((k) => ({ key: k, name: KIND_LABEL[k], value: totals[k] })).filter(
      (s) => s.value > 0,
    );
  }, [data]);
  const total = slices.reduce((a, s) => a + s.value, 0);

  return (
    <ChartFrame title="By kind" height={height}>
      {total === 0 ? (
        <EmptyChart height={height} />
      ) : (
        <div className="flex h-full items-center gap-4">
          <div className="h-full flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={slices} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="90%" paddingAngle={2} isAnimationActive={false}>
                  {slices.map((s) => (
                    <Cell key={s.key} fill={KIND_COLOR[s.key]} stroke="var(--card)" />
                  ))}
                </Pie>
                <Tooltip contentStyle={tooltipStyle} formatter={(v) => fmtNum(Number(v))} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-col gap-1.5 pr-1">
            {slices.map((s) => (
              <div key={s.key} className="flex items-center gap-2 text-xs">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ background: KIND_COLOR[s.key] }} />
                <span className="text-muted-foreground">{s.name}</span>
                <span className="ml-auto font-mono tabular-nums">
                  {total > 0 ? Math.round((s.value / total) * 100) : 0}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </ChartFrame>
  );
}
