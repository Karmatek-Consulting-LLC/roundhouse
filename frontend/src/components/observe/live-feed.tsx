import { useNavigate } from "react-router-dom";
import { Pause, Play, RefreshCw, Trash2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useObsFeed } from "@/hooks/use-obs-feed";
import type { ObsEvent } from "@/lib/api";
import { ConnectionLamp } from "./connection-lamp";
import { fmtClock, fmtMs, KIND_DOT } from "./format";

const VISIBLE = 400;

function FeedRow({ ev, showServer }: { ev: ObsEvent; showServer: boolean }) {
  const navigate = useNavigate();
  const isError = ev.status === "error";
  return (
    <button
      type="button"
      onClick={() => navigate(`/servers/${encodeURIComponent(ev.server_name)}/usage`)}
      title={ev.error ? `${ev.error}` : "Open server"}
      className={cn(
        "flex w-full animate-feed-in items-center gap-2 border-l-2 px-2.5 py-1.5 text-left font-mono text-[11px] transition-colors hover:bg-muted/50",
        isError ? "border-l-destructive bg-destructive/5" : "border-l-transparent",
      )}
    >
      <span className="shrink-0 tabular-nums text-muted-foreground">{fmtClock(ev.ts)}</span>
      {showServer && (
        <span className="w-28 shrink-0 truncate text-muted-foreground" title={ev.server_name}>
          {ev.server_name}
        </span>
      )}
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", KIND_DOT[ev.kind] ?? "bg-muted-foreground")} />
      <span className="flex-1 truncate text-foreground" title={ev.name}>
        {ev.name}
      </span>
      <span className="hidden w-32 shrink-0 truncate text-muted-foreground sm:block" title={ev.client_id ?? ""}>
        {ev.client_id ?? "—"}
      </span>
      <span
        className={cn(
          "w-16 shrink-0 text-right tabular-nums",
          isError ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {fmtMs(ev.duration_ms)}
      </span>
      <span className={cn("w-4 shrink-0 text-center", isError ? "text-destructive" : "text-emerald-500")}>
        {isError ? "✕" : "✓"}
      </span>
    </button>
  );
}

export function LiveFeed({ server, className }: { server?: string; className?: string }) {
  const { events, state, paused, buffered, pause, resume, clear, reconnect } = useObsFeed(server);
  const showServer = !server;
  const visible = events.slice(0, VISIBLE);

  return (
    <Card className={cn("flex flex-col overflow-hidden", className)}>
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="flex items-center gap-3">
          <h3 className="font-display text-sm font-bold uppercase tracking-[0.08em]">
            Live Traffic
          </h3>
          <ConnectionLamp state={state} paused={paused} />
          {paused && buffered > 0 && (
            <span className="font-mono text-[11px] text-amber-600 dark:text-amber-400">
              +{buffered} buffered
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2"
            onClick={() => (paused ? resume() : pause())}
            title={paused ? "Resume" : "Pause"}
          >
            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          </Button>
          <Button variant="outline" size="sm" className="h-7 px-2" onClick={clear} title="Clear">
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2"
            onClick={reconnect}
            title="Reconnect"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      <div className="min-h-[300px] flex-1 divide-y divide-border/40 overflow-y-auto max-h-[68vh]">
        {visible.length === 0 ? (
          <div className="flex h-[300px] flex-col items-center justify-center gap-1 text-center text-xs text-muted-foreground">
            <span>Idle on the turntable.</span>
            <span>Requests appear here the moment a client calls.</span>
          </div>
        ) : (
          visible.map((ev) => <FeedRow key={ev.id} ev={ev} showServer={showServer} />)
        )}
      </div>
      <div className="border-t px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {events.length >= VISIBLE ? `showing ${VISIBLE} of ${events.length}` : `${events.length} events`}
        {" · "}buffer cap 2000
      </div>
    </Card>
  );
}
