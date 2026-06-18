import { cn } from "@/lib/utils";
import type { FeedState } from "@/hooks/use-obs-feed";

/** Streaming-state indicator with a live copper pulse. Mirrors the LogsRail
 * lamp but reusable across the feed and the nav. */
export function ConnectionLamp({
  state,
  paused,
  className,
}: {
  state: FeedState;
  paused?: boolean;
  className?: string;
}) {
  let dot = "bg-muted-foreground";
  let label = "Closed";
  let pulse = false;
  let tone = "text-muted-foreground";

  if (paused) {
    dot = "bg-amber-500";
    label = "Paused";
    tone = "text-amber-600 dark:text-amber-400";
  } else if (state === "open") {
    dot = "bg-primary";
    label = "Streaming";
    pulse = true;
    tone = "text-primary";
  } else if (state === "connecting") {
    dot = "bg-amber-500";
    label = "Connecting";
    pulse = true;
    tone = "text-muted-foreground";
  } else if (state === "error") {
    dot = "bg-destructive";
    label = "Disconnected";
    tone = "text-destructive";
  }

  return (
    <span className={cn("inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider", tone, className)}>
      <span className="relative flex h-2 w-2">
        {pulse && (
          <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", dot)} />
        )}
        <span className={cn("relative inline-flex h-2 w-2 rounded-full", dot, pulse && "shadow-[0_0_8px] shadow-primary/60")} />
      </span>
      {label}
    </span>
  );
}
