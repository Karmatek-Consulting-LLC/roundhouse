import { cn } from "@/lib/utils";
import type { ObsRange } from "@/lib/api";

const RANGES: ObsRange[] = ["5m", "15m", "1h", "6h", "24h", "7d"];

export function RangeSelector({
  value,
  onChange,
}: {
  value: ObsRange;
  onChange: (r: ObsRange) => void;
}) {
  return (
    <div className="inline-flex items-center rounded-md border bg-muted/40 p-0.5 font-mono text-xs">
      {RANGES.map((r) => (
        <button
          key={r}
          type="button"
          onClick={() => onChange(r)}
          className={cn(
            "rounded px-2.5 py-1 uppercase tracking-wide transition-colors",
            value === r
              ? "bg-primary text-primary-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {r}
        </button>
      ))}
    </div>
  );
}
