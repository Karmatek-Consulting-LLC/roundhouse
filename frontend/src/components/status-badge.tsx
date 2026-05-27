import { Badge } from "@/components/ui/badge";
import type { ServerHealth } from "@/lib/api";

const statusStyles: Record<string, string> = {
  running: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  stopped: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  exited: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  created: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  not_deployed:
    "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-300",
  unknown: "bg-rose-100 text-rose-900 dark:bg-rose-900/30 dark:text-rose-300",
};

// Health override: an unhealthy container is still 'running' from Docker's
// POV but the user wants that surfaced loudly. `starting` is just the grace
// window; we keep the running colour but annotate the label.
const healthOverrideStyles: Partial<Record<NonNullable<ServerHealth>, string>> = {
  unhealthy: "bg-rose-100 text-rose-900 dark:bg-rose-900/30 dark:text-rose-300",
  starting: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
};

function formatStatusLabel(status: string, health?: ServerHealth): string {
  if (status === "not_deployed") return "not deployed";
  if (status === "unknown") return "docker unavailable";
  if (status === "running" && health === "unhealthy") return "unhealthy";
  if (status === "running" && health === "starting") return "starting";
  return status.replace(/_/g, " ");
}

export function StatusBadge({
  status,
  health,
}: {
  status: string;
  health?: ServerHealth;
}) {
  const override = status === "running" && health ? healthOverrideStyles[health] : undefined;
  return (
    <Badge variant="outline" className={override ?? statusStyles[status] ?? ""}>
      {formatStatusLabel(status, health)}
    </Badge>
  );
}
