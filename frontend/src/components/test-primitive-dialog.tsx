import { useEffect, useMemo, useState } from "react";
import {
  api,
  type McpPromptResult,
  type McpResourceResult,
  type McpToolResult,
  type Primitive,
  type ServerTokenSummary,
  type ToolParameter,
} from "@/lib/api";
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
import { Textarea } from "@/components/ui/textarea";
import { Play } from "lucide-react";

interface Props {
  serverName: string;
  primitive: Primitive;
  /** When the backing server isn't deployed, the live endpoint 409s - disable the button upstream. */
  disabled?: boolean;
}

type FormValue = string | boolean;

export function TestPrimitiveDialog({ serverName, primitive, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, FormValue>>({});
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  // Server tokens: once any exist, the spawned server requires auth, so the
  // backend runs the test as one of them ("Run as"). Default: oldest token.
  const [tokens, setTokens] = useState<ServerTokenSummary[]>([]);
  const [tokenName, setTokenName] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .listTokens(serverName)
      .then((t) => {
        if (cancelled) return;
        setTokens(t);
        setTokenName((cur) => (cur && t.some((x) => x.name === cur) ? cur : t[0]?.name ?? null));
      })
      .catch(() => {
        if (!cancelled) setTokens([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, serverName]);

  const paramFields = useMemo(() => {
    if (primitive.kind === "tool" || primitive.kind === "prompt") {
      return primitive.parameters;
    }
    if (primitive.kind === "resource_template") {
      // Extract {param} names from the URI template.
      const names = Array.from(primitive.uri_template.matchAll(/\{(\w+)\}/g)).map(
        (m) => m[1],
      );
      return names.map<ToolParameter>((name) => ({
        name,
        type: "str",
        description: `URI parameter ${name}`,
        required: true,
        default: null,
      }));
    }
    // Static resource - no params.
    return [] as ToolParameter[];
  }, [primitive]);

  function reset() {
    setValues({});
    setResult(null);
    setError(null);
  }

  function setField(name: string, value: FormValue) {
    setValues((v) => ({ ...v, [name]: value }));
  }

  function coerceArgs(): Record<string, unknown> | string {
    const out: Record<string, unknown> = {};
    for (const p of paramFields) {
      const raw = values[p.name];
      if (raw === undefined || raw === "") {
        if (p.required && p.default === null) {
          return `Missing required parameter: ${p.name}`;
        }
        continue;
      }
      switch (p.type) {
        case "int":
        case "float": {
          const num = Number(raw);
          if (Number.isNaN(num)) {
            return `${p.name}: not a number`;
          }
          out[p.name] = p.type === "int" ? Math.trunc(num) : num;
          break;
        }
        case "bool":
          out[p.name] = typeof raw === "boolean" ? raw : raw === "true";
          break;
        case "list":
        case "dict":
          try {
            out[p.name] = JSON.parse(String(raw));
          } catch {
            return `${p.name}: invalid JSON`;
          }
          break;
        default:
          out[p.name] = raw;
      }
    }
    return out;
  }

  async function handleRun() {
    setError(null);
    setResult(null);

    const args = coerceArgs();
    if (typeof args === "string") {
      setError(args);
      return;
    }

    setRunning(true);
    const asToken = tokenName ?? undefined;
    try {
      if (primitive.kind === "tool") {
        const r = await api.invokeTool(serverName, primitive.name, args, asToken);
        setResult(r);
      } else if (primitive.kind === "prompt") {
        const r = await api.getPrompt(serverName, primitive.name, args, asToken);
        setResult(r);
      } else if (primitive.kind === "resource") {
        const r = await api.readResource(serverName, primitive.uri, asToken);
        setResult(r);
      } else {
        // resource_template - substitute URI placeholders.
        let uri = primitive.uri_template;
        for (const [k, v] of Object.entries(args)) {
          uri = uri.replaceAll(`{${k}}`, String(v));
        }
        const r = await api.readResource(serverName, uri, asToken);
        setResult(r);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" disabled={disabled}>
          <Play className="h-3 w-3" />
        </Button>
      </DialogTrigger>
      {/* Near-fullscreen two-pane layout: params left, result right. Each pane
          scrolls independently so a long response never pushes the form out of
          reach — tweak args and re-run with the last result still visible. */}
      <DialogContent className="flex h-[88vh] w-[min(1200px,96vw)] max-w-none flex-col sm:max-w-none">
        <DialogHeader>
          <DialogTitle className="font-mono text-base">{primitive.name}</DialogTitle>
          <DialogDescription>
            {primitive.description || `Test the ${primitive.kind} live`}
          </DialogDescription>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-rows-[1fr_1fr] gap-4 md:grid-cols-2 md:grid-rows-1">
          <div className="flex min-h-0 flex-col gap-1.5">
            <Label className="text-xs text-muted-foreground">Parameters</Label>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto rounded border p-3">
              {paramFields.length === 0 && primitive.kind !== "resource" && (
                <p className="text-sm text-muted-foreground">No parameters.</p>
              )}
              {primitive.kind === "resource" && (
                <div className="text-sm">
                  <span className="text-muted-foreground">URI: </span>
                  <code className="rounded bg-muted px-1">{primitive.uri}</code>
                </div>
              )}
              {paramFields.map((p) => (
                <ParamInput
                  key={p.name}
                  param={p}
                  value={values[p.name]}
                  onChange={(v) => setField(p.name, v)}
                />
              ))}
            </div>
          </div>

          <div className="flex min-h-0 flex-col gap-1.5">
            <Label className="text-xs text-muted-foreground">Result</Label>
            <div className="min-h-0 flex-1 overflow-y-auto rounded border bg-muted/10 p-3">
              {error && (
                <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </div>
              )}
              {result !== null && <ResultView kind={primitive.kind} result={result} />}
              {result === null && !error && (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  {running ? "Running…" : "Run to see the result here."}
                </div>
              )}
            </div>
          </div>
        </div>

        <DialogFooter>
          {tokens.length > 0 && (
            <div className="flex items-center gap-2 sm:mr-auto" title="The token the platform authenticates this test with — scoped tokens are enforced exactly as for external clients.">
              <Label className="whitespace-nowrap text-xs text-muted-foreground">Run as</Label>
              <Select value={tokenName ?? undefined} onValueChange={setTokenName}>
                <SelectTrigger className="h-8 w-[180px] font-mono text-xs">
                  <SelectValue placeholder="token" />
                </SelectTrigger>
                <SelectContent>
                  {tokens.map((t) => (
                    <SelectItem key={t.id} value={t.name} className="font-mono text-xs">
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <Button variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
          <Button onClick={handleRun} disabled={running}>
            {running ? "Running..." : "Run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ParamInput({
  param,
  value,
  onChange,
}: {
  param: ToolParameter;
  value: FormValue | undefined;
  onChange: (v: FormValue) => void;
}) {
  const placeholder = param.default ?? "";

  if (param.type === "bool") {
    return (
      <div className="grid gap-1">
        <Label className="font-mono text-xs">
          {param.name}
          {!param.required && <span className="text-muted-foreground"> (optional)</span>}
        </Label>
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={value === true}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span className="text-sm text-muted-foreground">{param.description}</span>
        </div>
      </div>
    );
  }

  if (param.type === "list" || param.type === "dict") {
    return (
      <div className="grid gap-1">
        <Label className="font-mono text-xs">
          {param.name} <span className="text-muted-foreground">(JSON {param.type})</span>
        </Label>
        <Textarea
          rows={3}
          placeholder={param.type === "list" ? "[]" : "{}"}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
          className="font-mono text-xs"
        />
        {param.description && (
          <p className="text-xs text-muted-foreground">{param.description}</p>
        )}
      </div>
    );
  }

  return (
    <div className="grid gap-1">
      <Label className="font-mono text-xs">
        {param.name} <span className="text-muted-foreground">({param.type})</span>
        {!param.required && <span className="text-muted-foreground"> (optional)</span>}
      </Label>
      <Input
        type={param.type === "int" || param.type === "float" ? "number" : "text"}
        placeholder={placeholder}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      />
      {param.description && (
        <p className="text-xs text-muted-foreground">{param.description}</p>
      )}
    </div>
  );
}

function ResultView({ kind, result }: { kind: Primitive["kind"]; result: unknown }) {
  // Pull out the most user-useful slice based on response shape.
  const summary = summarize(kind, result);

  return (
    <div className="space-y-2">
      {summary && (
        <div className="rounded border bg-muted/30 px-3 py-2 text-sm whitespace-pre-wrap break-words">
          {summary}
        </div>
      )}
      {/* No summary to show -> the raw payload IS the result; start it open. */}
      <details className="rounded border" {...(summary == null ? { open: true } : {})}>
        <summary className="cursor-pointer px-3 py-1.5 text-xs text-muted-foreground">
          Raw response
        </summary>
        <pre className="overflow-x-auto bg-muted/20 p-3 text-xs">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function summarize(kind: Primitive["kind"], raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;

  if (kind === "tool") {
    const r = raw as McpToolResult;
    if (r.isError) {
      return `Error: ${r.content?.[0]?.text ?? "(no message)"}`;
    }
    if (r.structuredContent !== undefined) {
      const sc = r.structuredContent as Record<string, unknown>;
      // FastMCP wraps str returns as { result: "..." } - unwrap for readability.
      if (sc && typeof sc === "object" && "result" in sc && Object.keys(sc).length === 1) {
        return String(sc.result ?? "");
      }
      return JSON.stringify(sc, null, 2);
    }
    return r.content?.map((c) => c.text ?? "").join("\n") ?? null;
  }

  if (kind === "resource" || kind === "resource_template") {
    const r = raw as McpResourceResult;
    return r.contents?.map((c) => c.text ?? `(binary: ${c.mimeType})`).join("\n") ?? null;
  }

  if (kind === "prompt") {
    const r = raw as McpPromptResult;
    return (
      r.messages
        ?.map((m) => {
          const body = typeof m.content === "string" ? m.content : m.content?.text ?? "";
          return `[${m.role}]\n${body}`;
        })
        .join("\n\n") ?? null
    );
  }

  return null;
}
