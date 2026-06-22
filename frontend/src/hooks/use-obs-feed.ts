import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api, type ObsEvent } from "@/lib/api";

export type FeedState = "connecting" | "open" | "error" | "closed";

// Events are richer than log lines; cap lower than the 5000-line log buffer.
const MAX_FEED_EVENTS = 2000;
const BACKFILL = 200;

/**
 * Live request feed. Generalizes the LogsRail EventSource engine
 * (backfill -> stream, pause/resume buffer, reconnect, de-dupe, circular cap).
 * Events are kept newest-first for top-insert rendering.
 */
export function useObsFeed(server?: string) {
  const { user } = useAuth();
  const [events, setEvents] = useState<ObsEvent[]>([]);
  const [state, setState] = useState<FeedState>("connecting");
  const [paused, setPaused] = useState(false);
  const [buffered, setBuffered] = useState(0);
  const [streamKey, setStreamKey] = useState(0);

  const pausedRef = useRef(paused);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);
  const pausedBufferRef = useRef<ObsEvent[]>([]);
  const seenRef = useRef<Set<number>>(new Set());

  // Merge new events (any order) into the newest-first list; de-dupe + cap.
  const ingest = useCallback((incoming: ObsEvent[]) => {
    const seen = seenRef.current;
    const fresh = incoming.filter((e) => !seen.has(e.id));
    if (fresh.length === 0) return;
    fresh.forEach((e) => seen.add(e.id));
    fresh.sort((a, b) => b.id - a.id);
    setEvents((prev) => {
      const next = [...fresh, ...prev];
      if (next.length > MAX_FEED_EVENTS) next.length = MAX_FEED_EVENTS;
      return next;
    });
  }, []);

  useEffect(() => {
    if (!user) {
      setState("closed");
      return;
    }
    let cancelled = false;
    let es: EventSource | null = null;

    seenRef.current = new Set();
    pausedBufferRef.current = [];
    setEvents([]);
    setBuffered(0);
    setState("connecting");

    async function start() {
      let sinceId = 0;
      // Backfill recent history (already newest-first), then stream only newer.
      try {
        const page = await api.getObsFeed({ server, limit: BACKFILL });
        if (cancelled) return;
        sinceId = page.last_id;
        page.events.forEach((e) => seenRef.current.add(e.id));
        setEvents(page.events.slice(0, MAX_FEED_EVENTS));
      } catch {
        /* backfill is best-effort; the stream still works */
      }
      if (cancelled) return;

      const token = localStorage.getItem("token") ?? "";
      const params = new URLSearchParams({ token });
      if (server) params.set("server", server);
      if (sinceId) params.set("since_id", String(sinceId));
      es = new EventSource(`/api/observability/stream?${params.toString()}`);

      es.addEventListener("open", () => setState("open"));
      es.onopen = () => setState("open");
      es.onmessage = (evt) => {
        let parsed: ObsEvent;
        try {
          parsed = JSON.parse(evt.data) as ObsEvent;
        } catch {
          return;
        }
        if (pausedRef.current) {
          const buf = pausedBufferRef.current;
          buf.push(parsed);
          if (buf.length > MAX_FEED_EVENTS) buf.splice(0, buf.length - MAX_FEED_EVENTS);
          setBuffered(buf.length);
          return;
        }
        ingest([parsed]);
      };
      es.onerror = () => {
        setState("error");
        es?.close();
      };
    }
    start();

    return () => {
      cancelled = true;
      es?.close();
      setState("closed");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server, streamKey, user]);

  const pause = useCallback(() => setPaused(true), []);
  const resume = useCallback(() => {
    setPaused(false);
    const buf = pausedBufferRef.current;
    pausedBufferRef.current = [];
    setBuffered(0);
    if (buf.length) ingest(buf);
  }, [ingest]);
  const clear = useCallback(() => {
    seenRef.current = new Set();
    pausedBufferRef.current = [];
    setBuffered(0);
    setEvents([]);
  }, []);
  const reconnect = useCallback(() => setStreamKey((k) => k + 1), []);

  return { events, state, paused, buffered, pause, resume, clear, reconnect };
}
