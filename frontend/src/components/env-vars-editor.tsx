import { useState } from "react";
import type { EnvVar } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Lock, LockOpen, Plus, Settings, Trash2 } from "lucide-react";

interface EnvVarsEditorProps {
  envVars: EnvVar[];
  onChange: (envVars: EnvVar[]) => void;
  /** Defaults to "Environment Variables" */
  title?: string;
  /** Shown when the list is empty */
  hint?: string;
}

export function EnvVarsEditor({ envVars, onChange, title, hint }: EnvVarsEditorProps) {
  const heading = title ?? "Environment Variables";
  const emptyHint =
    hint ??
    "No environment variables configured. These are passed to the MCP server container at runtime.";
  // Per-row UI state: when a secret row has a stored value, we mask its
  // input behind a "Replace" affordance so plaintext never has to flow
  // back from the server. Tracked locally; doesn't leak into the spec.
  const [replacing, setReplacing] = useState<Record<number, boolean>>({});

  function addVar() {
    onChange([...envVars, { name: "", value: "", secret: false }]);
  }

  function removeVar(idx: number) {
    onChange(envVars.filter((_, i) => i !== idx));
    setReplacing((m) => {
      const next = { ...m };
      delete next[idx];
      return next;
    });
  }

  function updateVar(idx: number, patch: Partial<EnvVar>) {
    onChange(envVars.map((v, i) => (i === idx ? { ...v, ...patch } : v)));
  }

  function toggleSecret(idx: number) {
    const v = envVars[idx];
    const becomingSecret = !v.secret;
    // Flipping a secret-with-stored-value back to plain would still hide
    // the value from us; clear it so the row reads as fresh.
    if (!becomingSecret && v.has_value && !v.value) {
      updateVar(idx, { secret: false, value: "", has_value: false });
    } else {
      updateVar(idx, { secret: becomingSecret });
    }
    setReplacing((m) => {
      const next = { ...m };
      delete next[idx];
      return next;
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Settings className="h-4 w-4 text-muted-foreground" />
          <Label className="text-sm font-medium">{heading}</Label>
        </div>
        <Button variant="outline" size="sm" onClick={addVar}>
          <Plus className="mr-1 h-3 w-3" />
          Add
        </Button>
      </div>

      {envVars.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyHint}</p>
      ) : (
        <div className="space-y-2">
          {envVars.map((v, idx) => {
            const isSecret = !!v.secret;
            // A secret row with `has_value` and no local edit is in
            // "stored" mode - render a masked placeholder + Replace button
            // rather than an input field.
            const isStoredMasked = isSecret && !!v.has_value && !replacing[idx];
            return (
              <div key={idx} className="flex items-center gap-2">
                <Input
                  className="font-mono text-sm"
                  placeholder="VARIABLE_NAME"
                  value={v.name}
                  onChange={(e) =>
                    updateVar(idx, {
                      name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""),
                    })
                  }
                />
                <span className="text-muted-foreground">=</span>
                {isStoredMasked ? (
                  <div className="flex flex-1 items-center gap-2 rounded-md border bg-muted/40 px-3 py-1.5 text-xs">
                    <span className="font-mono text-muted-foreground">•••••••• (stored)</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto h-6 px-2 text-xs"
                      onClick={() => {
                        setReplacing((m) => ({ ...m, [idx]: true }));
                        updateVar(idx, { value: "" });
                      }}
                    >
                      Replace
                    </Button>
                  </div>
                ) : (
                  <Input
                    className="font-mono text-sm"
                    placeholder={isSecret ? "new secret value" : "value"}
                    type={isSecret ? "password" : "text"}
                    value={v.value}
                    onChange={(e) => updateVar(idx, { value: e.target.value })}
                  />
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => toggleSecret(idx)}
                  title={isSecret ? "Stored encrypted - click to make plain" : "Click to encrypt at rest"}
                  className={isSecret ? "text-amber-600 dark:text-amber-400" : ""}
                >
                  {isSecret ? <Lock className="h-4 w-4" /> : <LockOpen className="h-4 w-4" />}
                </Button>
                <Button variant="ghost" size="icon" onClick={() => removeVar(idx)}>
                  <Trash2 className="h-4 w-4 text-muted-foreground" />
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
