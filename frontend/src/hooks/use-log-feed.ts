import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api, type LogEvent, type LogFilters } from "@/lib/api";
import type { FeedState } from "@/hooks/use-obs-feed";

const MAX_FEED_EVENTS = 2000;
const BACKFILL = 200;
const OLDER_PAGE = 200;

/**
 * Live platform-log feed for the Logs console. Same engine as useObsFeed
 * (backfill -> SSE stream, pause/resume buffer, reconnect, de-dupe, circular
 * cap) but filter-aware: changing context/search/type/outcome restarts the
 * stream with the filters applied server-side. Newest-first.
 */
export function useLogFeed(filters: LogFilters) {
  const { user } = useAuth();
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [state, setState] = useState<FeedState>("connecting");
  const [paused, setPaused] = useState(false);
  const [buffered, setBuffered] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [streamKey, setStreamKey] = useState(0);

  const pausedRef = useRef(paused);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);
  const pausedBufferRef = useRef<LogEvent[]>([]);
  const seenRef = useRef<Set<number>>(new Set());

  const { context, q, event_type, outcome } = filters;

  // Merge new events (any order) into the newest-first list; de-dupe + cap.
  const ingest = useCallback((incoming: LogEvent[], append = false) => {
    const seen = seenRef.current;
    const fresh = incoming.filter((e) => !seen.has(e.id));
    if (fresh.length === 0) return;
    fresh.forEach((e) => seen.add(e.id));
    fresh.sort((a, b) => b.id - a.id);
    setEvents((prev) => {
      const next = append ? [...prev, ...fresh] : [...fresh, ...prev];
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
    setHasMore(false);
    setState("connecting");

    async function start() {
      let sinceId = 0;
      // Backfill recent history (already newest-first), then stream only newer.
      try {
        const page = await api.listLogEvents({
          context, q, event_type, outcome, limit: BACKFILL,
        });
        if (cancelled) return;
        sinceId = page.last_id;
        page.events.forEach((e) => seenRef.current.add(e.id));
        setEvents(page.events.slice(0, MAX_FEED_EVENTS));
        setHasMore(page.has_more);
      } catch {
        /* backfill is best-effort; the stream still works */
      }
      if (cancelled) return;

      const token = localStorage.getItem("token") ?? "";
      const params = new URLSearchParams({ token });
      if (context) params.set("context", context);
      if (q) params.set("q", q);
      if (event_type) params.set("event_type", event_type);
      if (outcome) params.set("outcome", outcome);
      if (sinceId) params.set("since_id", String(sinceId));
      es = new EventSource(`/api/logs/stream?${params.toString()}`);

      es.addEventListener("open", () => setState("open"));
      es.onopen = () => setState("open");
      es.onmessage = (evt) => {
        let parsed: LogEvent;
        try {
          parsed = JSON.parse(evt.data) as LogEvent;
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
  }, [context, q, event_type, outcome, streamKey, user]);

  const pause = useCallback(() => setPaused(true), []);
  const resume = useCallback(() => {
    setPaused(false);
    const buf = pausedBufferRef.current;
    pausedBufferRef.current = [];
    setBuffered(0);
    if (buf.length) ingest(buf);
  }, [ingest]);
  const reconnect = useCallback(() => setStreamKey((k) => k + 1), []);

  // Page older history below the current window ("Load older").
  const loadOlder = useCallback(async () => {
    setLoadingOlder(true);
    try {
      const oldest = events.length ? events[events.length - 1].id : undefined;
      const page = await api.listLogEvents({
        context, q, event_type, outcome, before_id: oldest, limit: OLDER_PAGE,
      });
      ingest(page.events, true);
      setHasMore(page.has_more);
    } finally {
      setLoadingOlder(false);
    }
  }, [context, q, event_type, outcome, events, ingest]);

  return {
    events, state, paused, buffered, hasMore, loadingOlder,
    pause, resume, reconnect, loadOlder,
  };
}
