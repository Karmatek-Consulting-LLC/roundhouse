// Shared formatters for the Observe console. Keep numerals compact + monospace.

export function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1_000_000) return trim(n / 1_000_000) + "M";
  if (Math.abs(n) >= 1_000) return trim(n / 1_000) + "k";
  return String(Math.round(n));
}

function trim(v: number): string {
  return (Math.round(v * 10) / 10).toString();
}

export function fmtMs(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1000) return (v / 1000).toFixed(2) + "s";
  if (v >= 100) return Math.round(v) + "ms";
  return (Math.round(v * 10) / 10) + "ms";
}

export function fmtPct(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (v > 0 && v < 0.1) return "<0.1%";
  return v.toFixed(v < 10 ? 2 : 1) + "%";
}

// HH:MM:SS for the live feed.
export function fmtClock(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

// Axis labels: time-of-day for intraday buckets, M/D HH:MM for multi-day.
export function fmtAxis(epochSec: number, bucketS: number): string {
  const d = new Date(epochSec * 1000);
  if (bucketS >= 3600) {
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function pad(n: number): string {
  return n < 10 ? "0" + n : String(n);
}

export const KIND_COLOR: Record<string, string> = {
  tool: "var(--chart-1)",
  resource: "var(--chart-2)",
  resource_template: "var(--chart-4)",
  prompt: "var(--chart-3)",
};

export const KIND_DOT: Record<string, string> = {
  tool: "bg-[var(--chart-1)]",
  resource: "bg-[var(--chart-2)]",
  resource_template: "bg-[var(--chart-4)]",
  prompt: "bg-[var(--chart-3)]",
};
