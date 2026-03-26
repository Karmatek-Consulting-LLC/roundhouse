import { useState } from "react";
import { api, type EnvVar } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, Save, Settings, Trash2 } from "lucide-react";

interface EnvVarsEditorProps {
  serverName: string;
  envVars: EnvVar[];
  onUpdated: () => void;
}

export function EnvVarsEditor({
  serverName,
  envVars,
  onUpdated,
}: EnvVarsEditorProps) {
  const [vars, setVars] = useState<EnvVar[]>(envVars);
  const [saving, setSaving] = useState(false);

  const dirty = JSON.stringify(vars) !== JSON.stringify(envVars);

  function addVar() {
    setVars([...vars, { name: "", value: "" }]);
  }

  function removeVar(idx: number) {
    setVars(vars.filter((_, i) => i !== idx));
  }

  function updateVar(idx: number, field: keyof EnvVar, value: string) {
    setVars(vars.map((v, i) => (i === idx ? { ...v, [field]: value } : v)));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const filtered = vars.filter((v) => v.name.trim());
      await api.updateEnvVars(serverName, filtered);
      onUpdated();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Settings className="h-4 w-4 text-muted-foreground" />
          <Label className="text-sm font-medium">Environment Variables</Label>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={addVar}>
            <Plus className="mr-1 h-3 w-3" />
            Add
          </Button>
          {dirty && (
            <Button size="sm" onClick={handleSave} disabled={saving}>
              <Save className="mr-1 h-3 w-3" />
              {saving ? "Deploying..." : "Save & Deploy"}
            </Button>
          )}
        </div>
      </div>

      {vars.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No environment variables configured. These are passed to the MCP server container at runtime.
        </p>
      ) : (
        <div className="space-y-2">
          {vars.map((v, idx) => (
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
                onFocus={(e) => e.target.type = "text"}
                onBlur={(e) => e.target.type = "password"}
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
