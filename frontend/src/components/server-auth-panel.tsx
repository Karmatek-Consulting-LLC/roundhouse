import { useCallback, useEffect, useState } from "react";
import {
  api,
  type MintedToken,
  type ServerScope,
  type ServerTokenSummary,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Copy, KeyRound, Loader2, Plus, Tag, Trash2, Check } from "lucide-react";

interface ServerAuthPanelProps {
  serverName: string;
  /** Called after any mutation so the parent can refresh the server (rebuild-flag display). */
  onMutated?: () => void;
}

export function ServerAuthPanel({ serverName, onMutated }: ServerAuthPanelProps) {
  const [scopes, setScopes] = useState<ServerScope[]>([]);
  const [tokens, setTokens] = useState<ServerTokenSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([
        api.listScopes(serverName),
        api.listTokens(serverName),
      ]);
      setScopes(s);
      setTokens(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load auth config");
    } finally {
      setLoading(false);
    }
  }, [serverName]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleMutated = () => {
    refresh();
    onMutated?.();
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading auth configuration...
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <ScopesSection
        serverName={serverName}
        scopes={scopes}
        onChanged={handleMutated}
      />

      <TokensSection
        serverName={serverName}
        scopes={scopes}
        tokens={tokens}
        onChanged={handleMutated}
      />
    </div>
  );
}

// ---------------- Scopes ----------------

function ScopesSection({
  serverName,
  scopes,
  onChanged,
}: {
  serverName: string;
  scopes: ServerScope[];
  onChanged: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setErr(null);
    setSubmitting(true);
    try {
      await api.createScope(serverName, { name, description: description || null });
      setName("");
      setDescription("");
      setCreating(false);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to create scope");
    } finally {
      setSubmitting(false);
    }
  }

  async function remove(scopeName: string) {
    if (!confirm(`Delete scope "${scopeName}"? This removes it from every token and primitive that references it.`)) {
      return;
    }
    try {
      await api.deleteScope(serverName, scopeName);
      onChanged();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to delete scope");
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-lg font-semibold">
            <Tag className="h-4 w-4" /> Scopes
          </h3>
          <p className="text-xs text-muted-foreground mt-1">
            Arbitrary tags you assign to tokens and primitives. A primitive with scopes can only be called by a token that holds all of them.
          </p>
        </div>
        {!creating && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="mr-1 h-3 w-3" /> New scope
          </Button>
        )}
      </div>

      {creating && (
        <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
          {err && (
            <p className="text-sm text-destructive">{err}</p>
          )}
          <div className="grid gap-2">
            <Label>Name</Label>
            <Input
              placeholder="read"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="grid gap-2">
            <Label>Description (optional)</Label>
            <Input
              placeholder="What does holding this scope grant?"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={submit} disabled={submitting || !name}>
              {submitting ? "Creating..." : "Create scope"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setCreating(false);
                setName("");
                setDescription("");
                setErr(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {scopes.length === 0 ? (
        <p className="rounded border border-dashed px-4 py-6 text-sm text-muted-foreground">
          No scopes yet. Without scopes, any valid token can call any primitive.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {scopes.map((s) => (
              <TableRow key={s.id}>
                <TableCell>
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{s.name}</code>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {s.description ?? <span className="italic opacity-60">—</span>}
                </TableCell>
                <TableCell>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => remove(s.name)}
                    title={`Delete ${s.name}`}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

// ---------------- Tokens ----------------

function TokensSection({
  serverName,
  scopes,
  tokens,
  onChanged,
}: {
  serverName: string;
  scopes: ServerScope[];
  tokens: ServerTokenSummary[];
  onChanged: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [minted, setMinted] = useState<MintedToken | null>(null);
  const [copied, setCopied] = useState(false);

  function togglePicked(scope: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }

  async function submit() {
    setErr(null);
    setSubmitting(true);
    try {
      const result = await api.mintToken(serverName, {
        name,
        scopes: Array.from(picked),
      });
      setMinted(result);
      setName("");
      setPicked(new Set());
      setCreating(false);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to mint token");
    } finally {
      setSubmitting(false);
    }
  }

  async function revoke(t: ServerTokenSummary) {
    if (!confirm(`Revoke token "${t.name}"? Clients using it will get 401 once the server is rebuilt.`)) {
      return;
    }
    try {
      await api.revokeToken(serverName, t.id);
      onChanged();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to revoke token");
    }
  }

  async function copyToken() {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard blocked - leave as-is, user can select manually
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-lg font-semibold">
            <KeyRound className="h-4 w-4" /> Tokens
          </h3>
          <p className="text-xs text-muted-foreground mt-1">
            Bearer tokens. Clients send <code className="rounded bg-muted px-1">Authorization: Bearer mcps_…</code>. Names are unique and immutable — to change a name or scopes, revoke and re-mint.
          </p>
        </div>
        {!creating && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="mr-1 h-3 w-3" /> New token
          </Button>
        )}
      </div>

      {creating && (
        <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
          {err && (
            <p className="text-sm text-destructive">{err}</p>
          )}
          <div className="grid gap-2">
            <Label>Name</Label>
            <p className="text-xs text-muted-foreground">
              Human label, surfaced as <code className="rounded bg-muted px-1">client_id</code> inside the server runtime. Unique per server and immutable.
            </p>
            <Input
              placeholder="CI Pipeline"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="grid gap-2">
            <Label>Scopes</Label>
            {scopes.length === 0 ? (
              <p className="text-xs text-muted-foreground italic">
                No scopes defined yet — this token will authenticate but can't be scope-gated. Define scopes above first if you want to gate primitives.
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {scopes.map((s) => {
                  const on = picked.has(s.name);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => togglePicked(s.name)}
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
          <div className="flex gap-2">
            <Button size="sm" onClick={submit} disabled={submitting || !name}>
              {submitting ? "Generating..." : "Generate token"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setCreating(false);
                setName("");
                setPicked(new Set());
                setErr(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {tokens.length === 0 ? (
        <p className="rounded border border-dashed px-4 py-6 text-sm text-muted-foreground">
          No tokens yet. With no tokens, the generated server runs unauthenticated.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Prefix</TableHead>
              <TableHead>Scopes</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tokens.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-medium">{t.name}</TableCell>
                <TableCell>
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                    {t.display_prefix}…
                  </code>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {t.scopes.length === 0 ? (
                      <span className="text-xs italic text-muted-foreground">none</span>
                    ) : (
                      t.scopes.map((s) => (
                        <Badge key={s} variant="outline" className="text-xs">
                          {s}
                        </Badge>
                      ))
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {t.created_at ? new Date(t.created_at).toLocaleString() : "—"}
                </TableCell>
                <TableCell>
                  <Button size="sm" variant="ghost" onClick={() => revoke(t)} title="Revoke">
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* Reveal-once dialog for a freshly-minted token. */}
      <Dialog open={!!minted} onOpenChange={(v) => { if (!v) setMinted(null); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Token created — copy it now</DialogTitle>
            <DialogDescription>
              This is the only time this token will ever be shown. The platform stores it encrypted and cannot retrieve the plaintext again.
            </DialogDescription>
          </DialogHeader>
          {minted && (
            <div className="space-y-3">
              <div className="rounded bg-muted p-3 font-mono text-xs break-all">
                {minted.token}
              </div>
              <Button onClick={copyToken} variant="outline" className="w-full">
                {copied ? (
                  <><Check className="mr-1 h-3 w-3" /> Copied</>
                ) : (
                  <><Copy className="mr-1 h-3 w-3" /> Copy to clipboard</>
                )}
              </Button>
              <p className="text-xs text-muted-foreground">
                Use it with HTTP header <code className="rounded bg-muted px-1">Authorization: Bearer {minted.token.substring(0, 12)}…</code>
              </p>
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setMinted(null)}>I've saved it</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
