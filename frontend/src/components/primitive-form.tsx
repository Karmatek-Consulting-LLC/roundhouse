import { useEffect, useMemo, useState } from "react";
import { api, type Primitive, type ServerScope, type ToolParameter } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus, Trash2 } from "lucide-react";
import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { useTheme } from "@/hooks/use-theme";
import { TestPrimitiveDialog } from "@/components/test-primitive-dialog";

type PrimitiveKind = "tool" | "resource" | "resource_template" | "prompt";

interface PrimitiveFormProps {
  serverName: string;
  existing?: Primitive;
  /** Called after a successful save (with the saved primitive name). */
  onSaved: (name: string) => void;
  /** Optional cancel handler - panel layout shows a Cancel button. */
  onCancel?: () => void;
  /** Visual layout tweak: 'panel' uses full-height code editor + no gap to footer. */
  layout?: "dialog" | "panel";
  /** When true and `existing` is set, show a Run button that opens the test dialog. */
  serverRunning?: boolean;
  /** When true, show a "testing last deploy" warning next to the Run button. */
  redeployPending?: boolean;
}

const EMPTY_PARAM: ToolParameter = {
  name: "",
  type: "str",
  description: "",
  required: true,
  default: null,
};

/**
 * Shared primitive-edit form. Owns all field state, scope discovery, and
 * the save call. The parent (modal or right-rail panel) just gives it a
 * server name + optional existing primitive and reacts to onSaved.
 */
export function PrimitiveForm({
  serverName,
  existing,
  onSaved,
  onCancel,
  layout = "dialog",
  serverRunning = false,
  redeployPending = false,
}: PrimitiveFormProps) {
  const isEdit = !!existing;
  const { resolvedTheme } = useTheme();
  const [kind, setKind] = useState<PrimitiveKind>(existing?.kind ?? "tool");
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [code, setCode] = useState(existing?.code ?? "");
  const [uri, setUri] = useState(
    existing?.kind === "resource"
      ? existing.uri
      : existing?.kind === "resource_template"
        ? existing.uri_template
        : ""
  );
  const [mimeType, setMimeType] = useState(
    existing?.kind === "resource" || existing?.kind === "resource_template"
      ? existing.mime_type
      : "text/plain"
  );
  const [params, setParams] = useState<ToolParameter[]>(
    existing && "parameters" in existing ? existing.parameters : []
  );
  const [returnType, setReturnType] = useState<"str" | "dict">(
    existing?.kind === "tool" && existing.return_type === "dict" ? "dict" : "str",
  );
  const [scopes, setScopes] = useState<string[]>(existing?.scopes ?? []);
  const [availableScopes, setAvailableScopes] = useState<ServerScope[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.listScopes(serverName).then(setAvailableScopes).catch(() => setAvailableScopes([]));
  }, [serverName]);

  function toggleScope(s: string) {
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  }

  function addParam() {
    setParams([...params, { ...EMPTY_PARAM }]);
  }
  function removeParam(idx: number) {
    setParams(params.filter((_, i) => i !== idx));
  }
  function updateParam(idx: number, field: keyof ToolParameter, value: string | boolean) {
    setParams(params.map((p, i) => (i === idx ? { ...p, [field]: value } : p)));
  }
  function updateParamFields(idx: number, updates: Partial<ToolParameter>) {
    setParams(params.map((p, i) => (i === idx ? { ...p, ...updates } : p)));
  }

  function buildPrimitive(): Primitive {
    const s = scopes.length ? scopes : undefined;
    switch (kind) {
      case "tool":
        return {
          kind: "tool", name, description, parameters: params, code,
          return_type: returnType, scopes: s,
        };
      case "resource":
        return { kind: "resource", name, uri, description, mime_type: mimeType, code, scopes: s };
      case "resource_template":
        return { kind: "resource_template", name, uri_template: uri, description, mime_type: mimeType, code, scopes: s };
      case "prompt":
        return { kind: "prompt", name, description, parameters: params, code, scopes: s };
    }
  }

  // Snapshot of the last-saved primitive (or the `existing` prop on first
  // mount) so we can show "Unsaved changes" and a Reset button consistent
  // with the other rails.
  const savedSnapshot = useMemo(
    () => (existing ? JSON.stringify(existing) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [existing?.name],
  );
  const [snapshot, setSnapshot] = useState<string | null>(savedSnapshot);
  const current = JSON.stringify(buildPrimitive());
  const dirty = snapshot === null ? !!name : current !== snapshot;

  function resetFields() {
    if (!existing) return;
    setKind(existing.kind);
    setName(existing.name);
    setDescription(existing.description ?? "");
    setCode(existing.code ?? "");
    setUri(
      existing.kind === "resource"
        ? existing.uri
        : existing.kind === "resource_template"
          ? existing.uri_template
          : "",
    );
    setMimeType(
      existing.kind === "resource" || existing.kind === "resource_template"
        ? existing.mime_type
        : "text/plain",
    );
    setParams("parameters" in existing ? existing.parameters : []);
    setReturnType(existing.kind === "tool" && existing.return_type === "dict" ? "dict" : "str");
    setScopes(existing.scopes ?? []);
    setError(null);
  }

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      const primitive = buildPrimitive();
      if (isEdit) {
        await api.updatePrimitive(serverName, existing!.name, primitive);
      } else {
        await api.addPrimitive(serverName, primitive);
      }
      setSnapshot(JSON.stringify(primitive));
      onSaved(primitive.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save primitive");
    } finally {
      setSaving(false);
    }
  }

  const showParams = kind === "tool" || kind === "prompt";
  const showUri = kind === "resource" || kind === "resource_template";
  const codeMinHeight = layout === "panel" ? "300px" : "200px";

  return (
    <div className="flex flex-col">
      <div className="grid gap-4 py-4">
        {!isEdit && (
          <div className="grid gap-2">
            <Label>Type</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as PrimitiveKind)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="tool">Tool</SelectItem>
                <SelectItem value="resource">Resource</SelectItem>
                <SelectItem value="resource_template">Resource Template</SelectItem>
                <SelectItem value="prompt">Prompt</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="grid gap-2">
          <Label>Name</Label>
          <Input
            placeholder="my_tool"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={isEdit}
          />
        </div>

        <div className="grid gap-2">
          <Label>Description</Label>
          <p className="text-xs text-muted-foreground">
            Passed to the LLM as context. Describe what it does, when to use it, and what it returns.
          </p>
          <Textarea
            className="min-h-[100px]"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        {kind === "tool" && (
          <div className="grid gap-2">
            <Label>Return type</Label>
            <Select value={returnType} onValueChange={(v) => setReturnType(v as "str" | "dict")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="str">Text (str)</SelectItem>
                <SelectItem value="dict">Object (dict)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}

        {showUri && (
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-2">
              <Label>{kind === "resource_template" ? "URI Template" : "URI"}</Label>
              <Input
                placeholder={kind === "resource_template" ? "users://{user_id}/profile" : "config://app-settings"}
                value={uri}
                onChange={(e) => setUri(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label>MIME Type</Label>
              <Input
                placeholder="text/plain"
                value={mimeType}
                onChange={(e) => setMimeType(e.target.value)}
              />
            </div>
          </div>
        )}

        {showParams && (
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>Parameters</Label>
              <Button variant="outline" size="sm" onClick={addParam}>
                <Plus className="mr-1 h-3 w-3" /> Add
              </Button>
            </div>
            {params.map((p, idx) => (
              <div key={idx} className="space-y-2 rounded-md border p-3">
                <div className="flex items-end gap-2">
                  <div className="flex-1">
                    <Label className="text-xs text-muted-foreground">Name</Label>
                    <Input value={p.name} onChange={(e) => updateParam(idx, "name", e.target.value)} />
                  </div>
                  <div className="w-[100px]">
                    <Label className="text-xs text-muted-foreground">Type</Label>
                    <Select value={p.type} onValueChange={(v) => updateParam(idx, "type", v)}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="str">str</SelectItem>
                        <SelectItem value="int">int</SelectItem>
                        <SelectItem value="float">float</SelectItem>
                        <SelectItem value="bool">bool</SelectItem>
                        <SelectItem value="list">list</SelectItem>
                        <SelectItem value="dict">dict</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Button variant="ghost" size="icon" onClick={() => removeParam(idx)}>
                    <Trash2 className="h-4 w-4 text-muted-foreground" />
                  </Button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label className="text-xs text-muted-foreground">Description</Label>
                    <Input
                      value={p.description}
                      onChange={(e) => updateParam(idx, "description", e.target.value)}
                    />
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Default value</Label>
                    <Input
                      placeholder="None (required)"
                      value={p.default ?? ""}
                      onChange={(e) => {
                        const val = e.target.value;
                        updateParamFields(idx, {
                          default: val === "" ? null : val,
                          required: val === "",
                        });
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="grid gap-2">
          <Label>Python Code</Label>
          <p className="text-xs text-muted-foreground">
            Function body only - parameters are defined above.
          </p>
          <div className="rounded-md border overflow-hidden">
            <CodeMirror
              value={code}
              onChange={setCode}
              theme={resolvedTheme}
              extensions={[python()]}
              minHeight={codeMinHeight}
              basicSetup={{
                lineNumbers: true,
                foldGutter: false,
                highlightActiveLine: true,
                indentOnInput: true,
                bracketMatching: true,
                autocompletion: true,
              }}
            />
          </div>
        </div>

        <div className="grid gap-2">
          <Label>Required scopes</Label>
          <p className="text-xs text-muted-foreground">
            The caller's token must hold <strong>all</strong> selected scopes. Empty = any valid token.
          </p>
          {availableScopes.length === 0 ? (
            <p className="text-xs italic text-muted-foreground">
              No scopes defined yet — add them under <strong>Auth</strong>.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {availableScopes.map((s) => {
                const on = scopes.includes(s.name);
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => toggleScope(s.name)}
                    className={`rounded-full border px-3 py-1 text-xs transition ${
                      on
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-background hover:bg-muted"
                    }`}
                  >
                    {s.name}
                  </button>
                );
              })}
            </div>
          )}
        </div>

      </div>

      <div className="border-t pt-3 pb-1 flex items-center gap-2">
        {isEdit && existing && (
          <div className="flex items-center gap-2">
            <TestPrimitiveDialog
              serverName={serverName}
              primitive={existing}
              disabled={!serverRunning}
            />
            {redeployPending && (
              <span
                className="text-xs italic text-amber-700 dark:text-amber-300"
                title="The deployed code may not match what you're editing - redeploy first to test the latest version."
              >
                ⚠ testing last deploy
              </span>
            )}
          </div>
        )}
        {error ? (
          <p className="text-xs text-destructive flex-1 text-right">{error}</p>
        ) : (
          <span className="text-xs text-muted-foreground flex-1 text-right">
            {dirty ? "Unsaved changes" : isEdit ? "Up to date" : ""}
          </span>
        )}
        {isEdit && (
          <Button variant="ghost" size="sm" onClick={resetFields} disabled={!dirty || saving}>
            Reset
          </Button>
        )}
        {onCancel && (
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        )}
        <Button onClick={handleSave} disabled={!name || !dirty || saving} size="sm">
          {saving ? "Saving..." : "Save"}
        </Button>
      </div>
    </div>
  );
}
