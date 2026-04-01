import { Badge } from "@/components/ui/badge";

const statusStyles: Record<string, string> = {
  running: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  stopped: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  exited: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  created: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  not_deployed:
    "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-300",
  unknown: "bg-rose-100 text-rose-900 dark:bg-rose-900/30 dark:text-rose-300",
};

function formatStatusLabel(status: string): string {
  if (status === "not_deployed") return "not deployed";
  if (status === "unknown") return "docker unavailable";
  return status.replace(/_/g, " ");
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={statusStyles[status] ?? ""}>
      {formatStatusLabel(status)}
    </Badge>
  );
}
