import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Boxes, Plus, X } from "lucide-react";

interface AptPackageManagerProps {
  packages: string[];
  onChange: (packages: string[]) => void;
}

// Mirrors the backend regex on /servers/{name}/apt-packages.
// Lets through versioned forms like libpq5=15.4-1 while keeping the apt CLI
// argv injection surface tight.
const APT_NAME = /^[a-zA-Z0-9][a-zA-Z0-9+._:=~-]*$/;

export function AptPackageManager({ packages, onChange }: AptPackageManagerProps) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  function add() {
    const v = value.trim();
    if (!v) return;
    if (!APT_NAME.test(v)) {
      setError(`"${v}" doesn't look like a valid package name`);
      return;
    }
    if (packages.includes(v)) {
      setError(`${v} is already in the list`);
      return;
    }
    onChange([...packages, v]);
    setValue("");
    setError(null);
  }

  function remove(name: string) {
    onChange(packages.filter((p) => p !== name));
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Boxes className="h-4 w-4 text-muted-foreground" />
        <Label className="text-sm font-medium">OS packages (apt)</Label>
      </div>
      <p className="text-xs text-muted-foreground">
        Versioned names like{" "}
        <code className="rounded bg-muted px-1">libpq5=15.4-1</code> are allowed.
      </p>

      <div className="flex gap-2">
        <Input
          placeholder="e.g. git, curl, libpq5"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button size="sm" onClick={add} disabled={!value.trim()}>
          <Plus className="mr-1 h-3 w-3" /> Add
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}

      {packages.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">
          No apt packages.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {packages.map((pkg) => (
            <Badge key={pkg} variant="secondary" className="gap-1 pl-2.5 pr-1 py-1">
              {pkg}
              <button
                className="ml-1 rounded-sm hover:bg-muted p-0.5"
                onClick={() => remove(pkg)}
                aria-label={`Remove ${pkg}`}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
