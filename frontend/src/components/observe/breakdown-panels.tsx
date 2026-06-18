import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useObsTop } from "@/hooks/use-obs-top";
import type { ObsRange, ObsRankedItem } from "@/lib/api";
import { fmtMs, fmtNum } from "./format";

type By = "tool" | "server" | "client";
const BY_OPTIONS: { key: By; label: string }[] = [
  { key: "tool", label: "Tools" },
  { key: "server", label: "Servers" },
  { key: "client", label: "Clients" },
];

function RankList({
  items,
  metric,
  accent,
}: {
  items: ObsRankedItem[];
  metric: (i: ObsRankedItem) => { value: string; weight: number };
  accent: string;
}) {
  if (items.length === 0) {
    return <div className="py-6 text-center text-xs text-muted-foreground">No data yet.</div>;
  }
  const max = Math.max(...items.map((i) => metric(i).weight), 1);
  return (
    <div className="flex flex-col gap-1.5">
      {items.map((i) => {
        const m = metric(i);
        return (
          <div key={i.key} className="group relative flex items-center gap-2 text-xs">
            <div className="relative min-w-0 flex-1">
              <div
                className="absolute inset-y-0 left-0 rounded-sm opacity-15 transition-all group-hover:opacity-25"
                style={{ width: `${(m.weight / max) * 100}%`, background: accent }}
              />
              <span className="relative block truncate px-1.5 py-1 font-mono" title={i.label}>
                {i.label}
              </span>
            </div>
            <span className="shrink-0 font-mono tabular-nums text-muted-foreground">{m.value}</span>
          </div>
        );
      })}
    </div>
  );
}

export function BreakdownPanels({
  range,
  server,
  className,
}: {
  range: ObsRange;
  server?: string;
  className?: string;
}) {
  const [by, setBy] = useState<By>("tool");
  const { data } = useObsTop(range, by, server);
  const ranked = data?.ranked ?? [];
  const errorLeaders = data?.error_leaders ?? [];
  const latencyLeaders = data?.latency_leaders ?? [];

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="font-display text-sm font-bold uppercase tracking-[0.08em] text-muted-foreground">
          Breakdown
        </CardTitle>
        <div className="inline-flex items-center rounded-md border bg-muted/40 p-0.5 font-mono text-[11px]">
          {BY_OPTIONS.map((o) => (
            <button
              key={o.key}
              type="button"
              onClick={() => setBy(o.key)}
              className={cn(
                "rounded px-2 py-0.5 transition-colors",
                by === o.key
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-6 md:grid-cols-3">
          <div>
            <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Most called
            </h4>
            <RankList
              items={ranked}
              accent="var(--primary)"
              metric={(i) => ({ value: fmtNum(i.calls), weight: i.calls })}
            />
          </div>
          <div>
            <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Error leaders
            </h4>
            <RankList
              items={errorLeaders}
              accent="var(--destructive)"
              metric={(i) => ({ value: fmtNum(i.errors), weight: i.errors })}
            />
          </div>
          <div>
            <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Slowest (p95)
            </h4>
            <RankList
              items={latencyLeaders}
              accent="var(--chart-3)"
              metric={(i) => ({ value: fmtMs(i.p95_ms), weight: i.p95_ms ?? 0 })}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
