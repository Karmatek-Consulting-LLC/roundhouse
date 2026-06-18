import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import type { Server } from "@/lib/api";
import { useObsTimeseries } from "@/hooks/use-obs-timeseries";
import { KpiTiles } from "./kpi-tiles";
import { TrafficChart } from "./charts";

/** Compact, historical traffic summary for the platform Dashboard. Layers the
 * persistent Observe data on top of the existing point-in-time usage cards. */
export function MiniTrafficStrip({ servers }: { servers: Server[] }) {
  const { data } = useObsTimeseries("1h");
  const running = servers.filter((s) => s.status === "running").length;

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-lg font-bold uppercase tracking-[0.08em]">
          Last hour
        </h2>
        <Link
          to="/observe"
          className="inline-flex items-center gap-1 font-mono text-xs uppercase tracking-wider text-primary hover:underline"
        >
          Live console <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
      <KpiTiles data={data} rangeSeconds={3600} runningServers={running} totalServers={servers.length} />
      <TrafficChart data={data} height={160} />
    </section>
  );
}
