import type { ServerHealth } from "@/lib/api";

// Departure-board style status lamp: glowing pip + mono uppercase label.
type Lamp = "green" | "amber" | "red" | "gray";

const lampDot: Record<Lamp, string> = {
  green: "bg-emerald-600 dark:bg-emerald-400 shadow-[0_0_8px] shadow-emerald-500/70",
  amber: "bg-amber-600 dark:bg-amber-400 shadow-[0_0_8px] shadow-amber-500/70 animate-pulse",
  red: "bg-rose-600 dark:bg-rose-400 shadow-[0_0_8px] shadow-rose-500/70",
  gray: "bg-zinc-400 dark:bg-zinc-500",
};

const lampText: Record<Lamp, string> = {
  green: "text-emerald-700 dark:text-emerald-400",
  amber: "text-amber-700 dark:text-amber-400",
  red: "text-rose-700 dark:text-rose-400",
  gray: "text-zinc-500 dark:text-zinc-400",
};

const statusLamp: Record<string, Lamp> = {
  running: "green",
  stopped: "gray",
  exited: "gray",
  created: "amber",
  not_deployed: "amber",
  unknown: "red",
};

// Health override: an unhealthy container is still 'running' from Docker's
// POV but the user wants that surfaced loudly. `starting` is just the grace
// window; we keep the lamp amber and annotate the label.
const healthLamp: Partial<Record<NonNullable<ServerHealth>, Lamp>> = {
  unhealthy: "red",
  starting: "amber",
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
  const override = status === "running" && health ? healthLamp[health] : undefined;
  const lamp = override ?? statusLamp[status] ?? "gray";
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-border bg-card px-2 py-0.5">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${lampDot[lamp]}`} />
      <span
        className={`font-mono text-[11px] font-medium uppercase tracking-wider ${lampText[lamp]}`}
      >
        {formatStatusLabel(status, health)}
      </span>
    </span>
  );
}
