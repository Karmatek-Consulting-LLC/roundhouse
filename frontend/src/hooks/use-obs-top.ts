import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api, type ObsRange, type ObsTop } from "@/lib/api";

const POLL_MS: Record<ObsRange, number> = {
  "5m": 8000,
  "15m": 15000,
  "1h": 20000,
  "6h": 30000,
  "24h": 60000,
  "7d": 300000,
};

export function useObsTop(
  range: ObsRange,
  by: "tool" | "server" | "client",
  server?: string,
) {
  const { user } = useAuth();
  const [data, setData] = useState<ObsTop | null>(null);
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
        const d = await api.getObsTop({ range, by, server });
        if (active) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Failed to load breakdown");
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
  }, [range, by, server, user]);

  return { data, loading, error };
}
