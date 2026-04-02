import { useMemo } from "react";
import type { EnvVar } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ChevronDown, Settings, Trash2 } from "lucide-react";

/** Fixed columns so Global/Local rows share the same alignment (badge, key, =, value, action). */
const ENV_ROW_GRID =
  "grid w-full grid-cols-[5.5rem_14rem_1.5rem_minmax(0,1fr)_2.5rem] items-center gap-x-3 gap-y-1";

export interface ServerEnvBindings {
  env_global_imports: string[];
  env_vars: EnvVar[];
}

interface ServerEnvBindingsEditorProps {
  value: ServerEnvBindings;
  onChange: (v: ServerEnvBindings) => void;
  /** Platform catalog (names + values for preview). */
  globalCatalog: EnvVar[];
}

function catalogMap(list: EnvVar[]): Map<string, string> {
  return new Map(list.map((e) => [e.name, e.value]));
}

export function ServerEnvBindingsEditor({
  value,
  onChange,
  globalCatalog,
}: ServerEnvBindingsEditorProps) {
  const gMap = useMemo(() => catalogMap(globalCatalog), [globalCatalog]);

  const globalAvailable = useMemo(() => {
    const taken = new Set(value.env_global_imports);
    return globalCatalog.filter((e) => e.name && !taken.has(e.name)).map((e) => e.name);
  }, [globalCatalog, value.env_global_imports]);

  function globalSelectOptions(currentName: string): string[] {
    const taken = new Set(value.env_global_imports.filter((n) => n !== currentName));
    const names = globalCatalog.map((e) => e.name).filter((n) => n && !taken.has(n));
    if (currentName && !names.includes(currentName)) {
      return [currentName, ...names];
    }
    return names;
  }

  const rowCount = value.env_global_imports.length + value.env_vars.length;

  function addGlobal(name: string) {
    if (!name.trim()) return;
    onChange({
      ...value,
      env_global_imports: [...value.env_global_imports, name.trim()],
    });
  }

  function addLocal() {
    onChange({
      ...value,
      env_vars: [...value.env_vars, { name: "", value: "" }],
    });
  }

  function removeGlobal(idx: number) {
    onChange({
      ...value,
      env_global_imports: value.env_global_imports.filter((_, i) => i !== idx),
    });
  }

  function removeLocal(idx: number) {
    onChange({
      ...value,
      env_vars: value.env_vars.filter((_, i) => i !== idx),
    });
  }

  function updateLocal(idx: number, field: keyof EnvVar, v: string) {
    onChange({
      ...value,
      env_vars: value.env_vars.map((e, i) =>
        i === idx ? { ...e, [field]: v } : e,
      ),
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Settings className="h-4 w-4 text-muted-foreground" />
          <Label className="text-sm font-medium">Environment variables</Label>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" type="button">
              Add
              <ChevronDown className="ml-1 h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuSub>
              <DropdownMenuSubTrigger disabled={globalCatalog.length === 0}>
                Global
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="max-h-64 overflow-y-auto">
                {globalAvailable.length === 0 ? (
                  <div className="px-2 py-1.5 text-xs text-muted-foreground">
                    {globalCatalog.length === 0
                      ? "No platform variables defined."
                      : "All are already added."}
                  </div>
                ) : (
                  globalAvailable.map((n) => (
                    <DropdownMenuItem
                      key={n}
                      className="font-mono text-xs"
                      onSelect={() => addGlobal(n)}
                    >
                      {n}
                    </DropdownMenuItem>
                  ))
                )}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuItem onSelect={() => addLocal()}>Local</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <p className="text-xs text-muted-foreground">
        Import platform-wide variables or add local-only pairs. Local values override the same name from
        global when the container runs.
      </p>

      {rowCount === 0 ? (
        <p className="text-sm text-muted-foreground">
          No variables bound. Use <strong>Add</strong> to import from platform settings or add local pairs.
        </p>
      ) : (
        <div className="overflow-x-auto pb-0.5">
          <div className="w-full min-w-[26rem] space-y-2">
          {value.env_global_imports.map((name, idx) => (
            <div key={`g-${name}-${idx}`} className={ENV_ROW_GRID}>
              <Badge variant="secondary" className="flex w-full justify-center">
                Global
              </Badge>
              <Select
                value={name}
                onValueChange={(v) => {
                  const next = [...value.env_global_imports];
                  next[idx] = v;
                  onChange({ ...value, env_global_imports: next });
                }}
              >
                <SelectTrigger className="w-full min-w-0 font-mono text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {globalSelectOptions(name).map((n) => (
                    <SelectItem key={n} value={n} className="font-mono text-sm">
                      {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span
                className="justify-self-center text-center text-sm leading-none text-muted-foreground select-none"
                aria-hidden
              >
                =
              </span>
              <Input
                readOnly
                className="min-w-0 font-mono text-sm"
                value={gMap.get(name) ?? "(missing in platform settings)"}
                title="Value comes from platform settings"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="justify-self-end"
                onClick={() => removeGlobal(idx)}
              >
                <Trash2 className="h-4 w-4 text-muted-foreground" />
              </Button>
            </div>
          ))}

          {value.env_vars.map((v, idx) => (
            <div key={`l-${idx}`} className={ENV_ROW_GRID}>
              <Badge variant="outline" className="flex w-full justify-center">
                Local
              </Badge>
              <Input
                className="min-w-0 font-mono text-sm"
                placeholder="VARIABLE_NAME"
                value={v.name}
                onChange={(e) =>
                  updateLocal(
                    idx,
                    "name",
                    e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""),
                  )
                }
              />
              <span
                className="justify-self-center text-center text-sm leading-none text-muted-foreground select-none"
                aria-hidden
              >
                =
              </span>
              <Input
                className="min-w-0 font-mono text-sm"
                placeholder="value"
                type="password"
                value={v.value}
                onChange={(e) => updateLocal(idx, "value", e.target.value)}
                onFocus={(e) => (e.target.type = "text")}
                onBlur={(e) => (e.target.type = "password")}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="justify-self-end"
                onClick={() => removeLocal(idx)}
              >
                <Trash2 className="h-4 w-4 text-muted-foreground" />
              </Button>
            </div>
          ))}
          </div>
        </div>
      )}
    </div>
  );
}
