import type { EnvVar } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, Settings, Trash2 } from "lucide-react";

interface EnvVarsEditorProps {
  envVars: EnvVar[];
  onChange: (envVars: EnvVar[]) => void;
}

export function EnvVarsEditor({ envVars, onChange }: EnvVarsEditorProps) {
  function addVar() {
    onChange([...envVars, { name: "", value: "" }]);
  }

  function removeVar(idx: number) {
    onChange(envVars.filter((_, i) => i !== idx));
  }

  function updateVar(idx: number, field: keyof EnvVar, value: string) {
    onChange(envVars.map((v, i) => (i === idx ? { ...v, [field]: value } : v)));
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Settings className="h-4 w-4 text-muted-foreground" />
          <Label className="text-sm font-medium">Environment Variables</Label>
        </div>
        <Button variant="outline" size="sm" onClick={addVar}>
          <Plus className="mr-1 h-3 w-3" />
          Add
        </Button>
      </div>

      {envVars.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No environment variables configured. These are passed to the MCP server container at runtime.
        </p>
      ) : (
        <div className="space-y-2">
          {envVars.map((v, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <Input
                className="font-mono text-sm"
                placeholder="VARIABLE_NAME"
                value={v.name}
                onChange={(e) =>
                  updateVar(idx, "name", e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""))
                }
              />
              <span className="text-muted-foreground">=</span>
              <Input
                className="font-mono text-sm"
                placeholder="value"
                type="password"
                value={v.value}
                onChange={(e) => updateVar(idx, "value", e.target.value)}
                onFocus={(e) => (e.target.type = "text")}
                onBlur={(e) => (e.target.type = "password")}
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => removeVar(idx)}
              >
                <Trash2 className="h-4 w-4 text-muted-foreground" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
