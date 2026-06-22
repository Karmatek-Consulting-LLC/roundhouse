import { useEffect, useMemo, useState } from "react";
import type { ObsRange, Server } from "@/lib/api";
import { useObsTimeseries } from "@/hooks/use-obs-timeseries";
import { RangeSelector } from "./range-selector";
import { KpiTiles } from "./kpi-tiles";
import { TrafficChart, LatencyChart, ErrorRateChart, KindDonut } from "./charts";
import { LiveFeed } from "./live-feed";
import { BreakdownPanels } from "./breakdown-panels";
import { FleetStatusCard } from "./fleet-status-card";

const RANGE_SECONDS: Record<ObsRange, number> = {
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "6h": 21600,
  "24h": 86400,
  "7d": 604800,
};

export function ObserveConsole({
  server,
  servers = [],
  compact = false,
  showFleet = false,
}: {
  /** When set, the whole console is scoped to one server (drilldown). */
  server?: string;
  servers?: Server[];
  /** Hide the page title (e.g. when embedded in the server editor). */
  compact?: boolean;
  /** Show the fleet-status widget (the unified landing dashboard). */
  showFleet?: boolean;
}) {
  // Internal server filter only meaningful for the global console.
  const [filter, setFilter] = useState<string | undefined>(server);
  useEffect(() => setFilter(server), [server]);

  const [range, setRange] = useState<ObsRange>("1h");
  const effectiveServer = server ?? filter;
  const { data } = useObsTimeseries(range, effectiveServer);

  const { running, total } = useMemo(() => {
    const scoped = effectiveServer ? servers.filter((s) => s.name === effectiveServer) : servers;
    return {
      running: scoped.filter((s) => s.status === "running").length,
      total: scoped.length,
    };
  }, [servers, effectiveServer]);

  const locked = !!server; // drilldown: filter is fixed

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        {!compact && (
          <div>
            <h1 className="font-display text-3xl font-extrabold uppercase tracking-[0.08em]">
              {showFleet ? (
                <>
                  Over<span className="text-primary">view</span>
                </>
              ) : (
                <>
                  Obser<span className="text-primary">ve</span>
                </>
              )}
            </h1>
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
              {showFleet
                ? "Realtime MCP traffic & fleet health"
                : "Realtime MCP traffic console"}
            </p>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {!locked && servers.length > 0 && (
            <select
              value={filter ?? ""}
              onChange={(e) => setFilter(e.target.value || undefined)}
              className="h-8 rounded-md border bg-background px-2 font-mono text-xs text-foreground"
            >
              <option value="">All servers</option>
              {servers.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          )}
          <RangeSelector value={range} onChange={setRange} />
        </div>
      </div>

      <KpiTiles
        data={data}
        rangeSeconds={RANGE_SECONDS[range]}
        runningServers={running}
        totalServers={total}
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <div className="space-y-4 xl:col-span-7">
          <TrafficChart data={data} />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <LatencyChart data={data} />
            <ErrorRateChart data={data} />
          </div>
        </div>
        {/* On xl the cell stretches to the left column's height; the feed fills
            it absolutely so its list scrolls internally instead of growing the
            whole grid. On smaller screens it stacks with a bounded height. */}
        <div className="relative min-h-[480px] xl:col-span-5">
          <LiveFeed server={effectiveServer} className="absolute inset-0" />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <KindDonut data={data} />
        <BreakdownPanels range={range} server={effectiveServer} className="lg:col-span-2" />
      </div>

      {showFleet && <FleetStatusCard servers={servers} />}
    </div>
  );
}
