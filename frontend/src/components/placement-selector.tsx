import { useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";

import { api, type NodeLabel, type PlacementConstraint } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";

function sameConstraint(a: PlacementConstraint, b: PlacementConstraint): boolean {
  return a.key === b.key && a.value === b.value;
}

interface PlacementSelectorProps {
  /** Currently selected node-label constraints. */
  selected: PlacementConstraint[];
  onChange: (next: PlacementConstraint[]) => void;
  disabled?: boolean;
}

/** Multi-select of node-label key=value pairs for Swarm placement. Options are
 * derived from the labels that actually exist on the cluster (never free-form);
 * selecting several ANDs them. Hidden entirely when the backend isn't a Swarm.
 * Any already-selected constraint whose label has since disappeared is still
 * shown (so it can be removed) and marked stale. */
export function PlacementSelector({ selected, onChange, disabled }: PlacementSelectorProps) {
  const [labels, setLabels] = useState<NodeLabel[]>([]);
  const [supported, setSupported] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    api
      .listNodeLabels()
      .then((r) => {
        if (!active) return;
        setSupported(r.supported);
        setLabels(r.labels);
      })
      .catch(() => {
        if (active) setSupported(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Not a Swarm (or the probe failed): placement selection doesn't apply, so
  // render nothing rather than an empty control.
  if (supported === false) return null;

  function toggle(c: PlacementConstraint) {
    const has = selected.some((s) => sameConstraint(s, c));
    onChange(has ? selected.filter((s) => !sameConstraint(s, c)) : [...selected, c]);
  }

  // Selected-but-vanished labels: keep them visible so the user can clear them.
  const stale = selected.filter((s) => !labels.some((l) => sameConstraint(l, s)));

  return (
    <div className="grid gap-2">
      <Label>Node placement</Label>
      <p className="text-xs text-muted-foreground">
        Restrict this server's tasks to Swarm nodes carrying the selected labels.
        Choosing several requires <strong>all</strong> to match. None = schedule anywhere.
      </p>

      {supported === null ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> Loading node labels…
        </div>
      ) : labels.length === 0 && stale.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">
          No node labels defined on this swarm. Add labels with{" "}
          <code>docker node update --label-add key=value &lt;node&gt;</code> to enable placement.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {labels.map((l) => {
            const active = selected.some((s) => sameConstraint(s, l));
            return (
              <button
                key={`${l.key}=${l.value}`}
                type="button"
                disabled={disabled}
                onClick={() => toggle({ key: l.key, value: l.value })}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50",
                  active
                    ? "border-transparent bg-primary text-primary-foreground hover:bg-primary/80"
                    : "border-input bg-background hover:bg-accent hover:text-accent-foreground",
                )}
                title={`${l.nodes} node${l.nodes === 1 ? "" : "s"} carry this label`}
              >
                {active && <Check className="h-3 w-3" />}
                <span className="font-mono">
                  {l.key}={l.value}
                </span>
                <span className={cn("tabular-nums", active ? "opacity-80" : "text-muted-foreground")}>
                  ({l.nodes})
                </span>
              </button>
            );
          })}
          {stale.map((s) => (
            <button
              key={`stale-${s.key}=${s.value}`}
              type="button"
              disabled={disabled}
              onClick={() => toggle(s)}
              className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/50 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-700 transition-colors disabled:opacity-50 dark:text-amber-400"
              title="This label no longer exists on any node — click to remove"
            >
              <Check className="h-3 w-3" />
              <span className="font-mono">
                {s.key}={s.value}
              </span>
              <span className="opacity-80">(stale)</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
