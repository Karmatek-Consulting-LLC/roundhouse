import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Code, Plus, Trash2 } from "lucide-react";

interface ImportsEditorProps {
  imports: string[];
  onChange: (imports: string[]) => void;
}

export function ImportsEditor({ imports, onChange }: ImportsEditorProps) {
  function addImport() {
    onChange([...imports, ""]);
  }

  function removeImport(idx: number) {
    onChange(imports.filter((_, i) => i !== idx));
  }

  function updateImport(idx: number, value: string) {
    onChange(imports.map((v, i) => (i === idx ? value : v)));
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Code className="h-4 w-4 text-muted-foreground" />
          <Label className="text-sm font-medium">Python Imports</Label>
        </div>
        <Button variant="outline" size="sm" onClick={addImport}>
          <Plus className="mr-1 h-3 w-3" />
          Add
        </Button>
      </div>

      {imports.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No imports configured. Add imports that all primitives in this server can use.
        </p>
      ) : (
        <div className="space-y-2">
          {imports.map((imp, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <Input
                className="font-mono text-sm"
                placeholder="import requests"
                value={imp}
                onChange={(e) => updateImport(idx, e.target.value)}
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => removeImport(idx)}
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
