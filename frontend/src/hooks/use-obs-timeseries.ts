import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api, type ObsRange, type ObsTimeseries } from "@/lib/api";

// Poll cadence scaled to the window: tight ranges feel live, wide ranges are cheap.
const POLL_MS: Record<ObsRange, number> = {
  "5m": 5000,
  "15m": 10000,
  "1h": 15000,
  "6h": 30000,
  "24h": 60000,
  "7d": 300000,
};

export function useObsTimeseries(range: ObsRange, server?: string) {
  const { user } = useAuth();
  // Keep the prior snapshot during a refetch so range switches don't blank.
  const [data, setData] = useState<ObsTimeseries | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) {
      setLoading(false);
      return;
    }
    let active = true;
    async function tick() {
      try {
        const d = await api.getObsTimeseries({ range, server });
        if (active) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Failed to load metrics");
      } finally {
        if (active) setLoading(false);
      }
    }
    setLoading(true);
    tick();
    const timer = window.setInterval(tick, POLL_MS[range]);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [range, server, user]);

  return { data, loading, error };
}
