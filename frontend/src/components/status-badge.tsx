import { Badge } from "@/components/ui/badge";

const statusStyles: Record<string, string> = {
  running: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  exited: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  created: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={statusStyles[status] ?? ""}>
      {status}
    </Badge>
  );
}
