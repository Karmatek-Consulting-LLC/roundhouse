import { useState } from "react";
import { api, type Template } from "@/lib/api";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface CreateServerDialogProps {
  templates: Template[];
  onCreated: () => void;
}

export function CreateServerDialog({
  templates,
  onCreated,
}: CreateServerDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const selectedTemplate = templates.find((t) => t.name === templateName);

  function reset() {
    setName("");
    setTemplateName("");
    setConfig({});
    setError(null);
  }

  async function handleCreate() {
    setError(null);
    setCreating(true);
    try {
      await api.createServer({ name, template: templateName, config });
      setOpen(false);
      reset();
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create server");
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) reset();
      }}
    >
      <DialogTrigger render={<Button />}>
        Create Server
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create MCP Server</DialogTitle>
          <DialogDescription>
            Deploy a new MCP server from a template.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="server-name">Server Name</Label>
            <Input
              id="server-name"
              placeholder="my-server"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label>Template</Label>
            <Select
              value={templateName}
              onValueChange={(val) => setTemplateName(val ?? "")}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a template" />
              </SelectTrigger>
              <SelectContent>
                {templates.map((t) => (
                  <SelectItem key={t.name} value={t.name}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedTemplate && (
              <p className="text-sm text-muted-foreground">
                {selectedTemplate.description}
              </p>
            )}
          </div>

          {selectedTemplate?.variables.map((v) => (
            <div key={v.name} className="grid gap-2">
              <Label htmlFor={`var-${v.name}`}>{v.name}</Label>
              <Input
                id={`var-${v.name}`}
                placeholder={v.default ?? ""}
                value={config[v.name] ?? ""}
                onChange={(e) =>
                  setConfig((prev) => ({ ...prev, [v.name]: e.target.value }))
                }
              />
              <p className="text-xs text-muted-foreground">{v.description}</p>
            </div>
          ))}

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button
            onClick={handleCreate}
            disabled={!name || !templateName || creating}
          >
            {creating ? "Creating..." : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
