import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

/** Small icon button that copies `value` to the clipboard and briefly shows a
 * check. Reused anywhere we render a URL/token the user will want to grab. */
export function CopyButton({
  value,
  title = "Copy to clipboard",
  className,
}: {
  value: string;
  title?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy(e: React.MouseEvent) {
    // Endpoints often sit inside clickable rows; don't trigger the row.
    e.stopPropagation();
    e.preventDefault();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked (e.g. non-secure context) — user can select manually
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      title={copied ? "Copied!" : title}
      aria-label={title}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
        className,
      )}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
  );
}
