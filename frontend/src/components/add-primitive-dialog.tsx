import { useState } from "react";
import { api, type Primitive, type ToolParameter } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
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

type PrimitiveKind = "tool" | "resource" | "resource_template" | "prompt";

interface AddPrimitiveDialogProps {
  serverName: string;
  onAdded: () => void;
  existing?: Primitive;
}

const EMPTY_PARAM: ToolParameter = {
  name: "",
  type: "str",
  description: "",
  required: true,
  default: null,
};

export function AddPrimitiveDialog({
  serverName,
  onAdded,
  existing,
}: AddPrimitiveDialogProps) {
  const isEdit = !!existing;
  const [open, setOpen] = useState(false);
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
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function reset() {
    if (!isEdit) {
      setKind("tool");
      setName("");
      setDescription("");
      setCode("");
      setUri("");
      setMimeType("text/plain");
      setParams([]);
    }
    setError(null);
  }

  function addParam() {
    setParams([...params, { ...EMPTY_PARAM }]);
  }

  function removeParam(idx: number) {
    setParams(params.filter((_, i) => i !== idx));
  }

  function updateParam(idx: number, field: keyof ToolParameter, value: string | boolean) {
    setParams(
      params.map((p, i) =>
        i === idx ? { ...p, [field]: value } : p
      )
    );
  }

  function buildPrimitive(): Primitive {
    switch (kind) {
      case "tool":
        return { kind: "tool", name, description, parameters: params, code };
      case "resource":
        return { kind: "resource", name, uri, description, mime_type: mimeType, code };
      case "resource_template":
        return { kind: "resource_template", name, uri_template: uri, description, mime_type: mimeType, code };
      case "prompt":
        return { kind: "prompt", name, description, parameters: params, code };
    }
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
      setOpen(false);
      reset();
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save primitive");
    } finally {
      setSaving(false);
    }
  }

  const showParams = kind === "tool" || kind === "prompt";
  const showUri = kind === "resource" || kind === "resource_template";

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) reset();
      }}
    >
      <DialogTrigger asChild>
        {isEdit ? (
          <Button variant="outline" size="sm">Edit</Button>
        ) : (
          <Button size="sm">
            <Plus className="mr-1 h-4 w-4" />
            Add Primitive
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit" : "Add"} Primitive</DialogTitle>
          <DialogDescription>
            Define an MCP {kind} with its Python implementation.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          {!isEdit && (
            <div className="grid gap-2">
              <Label>Type</Label>
              <Select value={kind} onValueChange={(v) => setKind(v as PrimitiveKind)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
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
              This is passed to the LLM as context. Be detailed about what the primitive does,
              when to use it, expected inputs/outputs, and any important behavior.
            </p>
            <Textarea
              className="min-h-[120px]"
              placeholder={"Describe what this tool does, when an LLM should use it, what it returns, and any constraints or edge cases."}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {showUri && (
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>{kind === "resource_template" ? "URI Template" : "URI"}</Label>
                <Input
                  placeholder={
                    kind === "resource_template"
                      ? "users://{user_id}/profile"
                      : "config://app-settings"
                  }
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
                  <Plus className="mr-1 h-3 w-3" />
                  Add
                </Button>
              </div>
              {params.map((p, idx) => (
                <div key={idx} className="grid grid-cols-[1fr_100px_1fr_auto] gap-2 items-end">
                  <div>
                    <Label className="text-xs text-muted-foreground">Name</Label>
                    <Input
                      placeholder="param_name"
                      value={p.name}
                      onChange={(e) => updateParam(idx, "name", e.target.value)}
                    />
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Type</Label>
                    <Select
                      value={p.type}
                      onValueChange={(v) => updateParam(idx, "type", v)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
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
                  <div>
                    <Label className="text-xs text-muted-foreground">Description</Label>
                    <Input
                      placeholder="What this param does"
                      value={p.description}
                      onChange={(e) => updateParam(idx, "description", e.target.value)}
                    />
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => removeParam(idx)}
                  >
                    <Trash2 className="h-4 w-4 text-muted-foreground" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          <div className="grid gap-2">
            <Label>Python Code</Label>
            <p className="text-xs text-muted-foreground">
              Function body only -- parameters are defined above.
              Use <code className="rounded bg-muted px-1">return</code> to send the result.
            </p>
            <Textarea
              className="font-mono text-sm min-h-[200px]"
              placeholder={'return f"Hello, {name}!"'}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button onClick={handleSave} disabled={!name || saving}>
            {saving ? "Deploying..." : isEdit ? "Update & Deploy" : "Add & Deploy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
