import { useCallback, useEffect, useState } from "react";
import { api, type RoleMapping, type SsoConfig, type Team } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Pencil, Plus, Save, ShieldCheck, Trash2 } from "lucide-react";

// Radix Select forbids an empty-string item value, so represent "no team" with
// a sentinel and translate at the API boundary.
const NO_TEAM = "__none__";

type FormState = {
  entra_app_role: string;
  roundhouse_role: "user" | "superadmin";
  team_id: string;
  team_role: "admin" | "member";
};

const EMPTY_FORM: FormState = {
  entra_app_role: "",
  roundhouse_role: "user",
  team_id: NO_TEAM,
  team_role: "member",
};

/**
 * Platform Settings → Entra ID SSO. Two cards:
 *  - Connection: the OIDC connection settings (tenant/client/secret/redirect),
 *    stored in platform settings (NOT env). The secret is write-only.
 *  - Role mappings: Entra app role → Roundhouse grant table read on every login.
 * Superadmin-gated by the route that renders Settings.
 */
export function SsoSettingsCard() {
  const [config, setConfig] = useState<SsoConfig | null>(null);
  const [mappings, setMappings] = useState<RoleMapping[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);

  // Connection form
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [redirectUri, setRedirectUri] = useState("");
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const [savingConn, setSavingConn] = useState(false);
  const [connError, setConnError] = useState<string | null>(null);

  // Mapping dialog
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<RoleMapping | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyConfig = useCallback((cfg: SsoConfig) => {
    setConfig(cfg);
    setTenantId(cfg.entra_tenant_id);
    setClientId(cfg.entra_client_id);
    // Prefill the redirect URI suggestion when none is set yet.
    setRedirectUri(cfg.entra_redirect_uri || cfg.suggested_redirect_uri);
    setSecret("");
    setClearSecret(false);
  }, []);

  const refresh = useCallback(async () => {
    const [cfg, rows, teamList] = await Promise.all([
      api.getSsoConfig(),
      api.listRoleMappings(),
      api.listTeams().catch(() => [] as Team[]),
    ]);
    applyConfig(cfg);
    setMappings(rows);
    setTeams(teamList);
  }, [applyConfig]);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  async function handleSaveConnection() {
    setConnError(null);
    setSavingConn(true);
    try {
      const body: {
        entra_tenant_id: string;
        entra_client_id: string;
        entra_redirect_uri: string;
        entra_client_secret?: string;
      } = {
        entra_tenant_id: tenantId.trim(),
        entra_client_id: clientId.trim(),
        entra_redirect_uri: redirectUri.trim(),
      };
      // Write-only secret: send the new value, or "" to clear; omit to keep.
      if (secret.length > 0) body.entra_client_secret = secret;
      else if (clearSecret) body.entra_client_secret = "";
      applyConfig(await api.updateSsoConfig(body));
    } catch (e) {
      setConnError(e instanceof Error ? e.message : "Failed to save SSO settings");
    } finally {
      setSavingConn(false);
    }
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setError(null);
    setDialogOpen(true);
  }

  function openEdit(m: RoleMapping) {
    setEditing(m);
    setForm({
      entra_app_role: m.entra_app_role,
      roundhouse_role: m.roundhouse_role,
      team_id: m.team_id ?? NO_TEAM,
      team_role: m.team_role,
    });
    setError(null);
    setDialogOpen(true);
  }

  async function handleSaveMapping() {
    setError(null);
    setSaving(true);
    try {
      const body = {
        entra_app_role: form.entra_app_role.trim(),
        roundhouse_role: form.roundhouse_role,
        team_id: form.team_id === NO_TEAM ? null : form.team_id,
        team_role: form.team_role,
      };
      if (editing) {
        await api.updateRoleMapping(editing.id, body);
      } else {
        await api.createRoleMapping(body);
      }
      setDialogOpen(false);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save mapping");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(m: RoleMapping) {
    if (!confirm(`Delete the mapping for "${m.entra_app_role}"?`)) return;
    await api.deleteRoleMapping(m.id);
    refresh();
  }

  const teamName = (id: string | null) =>
    id ? teams.find((t) => t.id === id)?.name ?? "(deleted team)" : "—";

  const secretConfigured = config?.entra_client_secret_configured ?? false;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            Entra ID SSO
            {config && (
              <Badge variant={config.enabled ? "default" : "secondary"}>
                {config.enabled ? "Enabled" : "Not configured"}
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            Connect a single-tenant Entra ID app so users can sign in with
            Microsoft. Register the redirect URI below on the Entra app, then
            paste the tenant ID, client ID, and a client secret. The secret is
            encrypted at rest. SSO turns on once all four values are set.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading ? (
            <div className="py-8 text-center text-muted-foreground">Loading…</div>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="grid gap-2 min-w-0">
                  <Label htmlFor="entra-tenant">Tenant ID</Label>
                  <Input
                    id="entra-tenant"
                    value={tenantId}
                    onChange={(e) => setTenantId(e.target.value)}
                    placeholder="00000000-0000-0000-0000-000000000000"
                    className="font-mono text-sm"
                    autoComplete="off"
                  />
                </div>
                <div className="grid gap-2 min-w-0">
                  <Label htmlFor="entra-client">Client ID</Label>
                  <Input
                    id="entra-client"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    placeholder="00000000-0000-0000-0000-000000000000"
                    className="font-mono text-sm"
                    autoComplete="off"
                  />
                </div>
              </div>
              <div className="grid gap-2 min-w-0">
                <Label htmlFor="entra-secret">Client secret</Label>
                <Input
                  id="entra-secret"
                  type="password"
                  value={secret}
                  onChange={(e) => {
                    setSecret(e.target.value);
                    if (e.target.value.length > 0) setClearSecret(false);
                  }}
                  placeholder={
                    secretConfigured
                      ? "Leave blank to keep saved secret"
                      : "Entra app client secret value"
                  }
                  className="font-mono text-sm"
                  autoComplete="new-password"
                />
                {secretConfigured && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-auto px-0 py-1 text-xs text-muted-foreground hover:text-destructive"
                    onClick={() => {
                      setClearSecret(true);
                      setSecret("");
                    }}
                  >
                    Clear saved secret
                  </Button>
                )}
                {clearSecret && (
                  <p className="text-xs text-amber-700 dark:text-amber-400">
                    Saved secret will be removed on save.
                  </p>
                )}
              </div>
              <div className="grid gap-2 min-w-0">
                <Label htmlFor="entra-redirect">Redirect URI</Label>
                <Input
                  id="entra-redirect"
                  value={redirectUri}
                  onChange={(e) => setRedirectUri(e.target.value)}
                  className="font-mono text-sm"
                  autoComplete="off"
                />
                <p className="text-xs text-muted-foreground">
                  Must exactly match a redirect URI registered on the Entra app.
                </p>
              </div>
              {connError && <p className="text-sm text-destructive">{connError}</p>}
              <Button size="sm" onClick={handleSaveConnection} disabled={savingConn}>
                <Save className="mr-1 h-4 w-4" />
                {savingConn ? "Saving…" : "Save connection"}
              </Button>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Role mappings</CardTitle>
          <CardDescription>
            Map Entra <strong>app roles</strong> (from the token's{" "}
            <code className="rounded bg-muted px-1">roles</code> claim) to a
            Roundhouse role and optional team. Applied on every SSO sign-in; Entra
            is authoritative for SSO users. A user whose roles match no mapping
            signs in with the lowest privilege (
            <code className="rounded bg-muted px-1">user</code>).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex justify-end">
            <Button size="sm" onClick={openCreate}>
              <Plus className="mr-1 h-4 w-4" />
              Add mapping
            </Button>
          </div>

          {loading ? (
            <div className="py-8 text-center text-muted-foreground">Loading…</div>
          ) : mappings.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No mappings yet. Add one to grant SSO users a role or team.
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Entra app role</TableHead>
                    <TableHead>Roundhouse role</TableHead>
                    <TableHead>Team</TableHead>
                    <TableHead>Team role</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {mappings.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="font-mono text-sm">
                        {m.entra_app_role}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            m.roundhouse_role === "superadmin" ? "default" : "secondary"
                          }
                        >
                          {m.roundhouse_role}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {teamName(m.team_id)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {m.team_id ? m.team_role : "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            title="Edit mapping"
                            aria-label={`Edit mapping ${m.entra_app_role}`}
                            onClick={() => openEdit(m)}
                          >
                            <Pencil className="h-3 w-3" />
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            title="Delete mapping"
                            aria-label={`Delete mapping ${m.entra_app_role}`}
                            onClick={() => handleDelete(m)}
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit mapping" : "Add mapping"}</DialogTitle>
            <DialogDescription>
              Map an Entra app role to a Roundhouse grant.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label>Entra app role</Label>
              <Input
                value={form.entra_app_role}
                onChange={(e) =>
                  setForm((f) => ({ ...f, entra_app_role: e.target.value }))
                }
                placeholder="Roundhouse.Admin"
                className="font-mono text-sm"
              />
            </div>
            <div className="grid gap-2">
              <Label>Roundhouse role</Label>
              <Select
                value={form.roundhouse_role}
                onValueChange={(v) =>
                  setForm((f) => ({ ...f, roundhouse_role: v as FormState["roundhouse_role"] }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">User</SelectItem>
                  <SelectItem value="superadmin">SuperAdmin</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Team (optional)</Label>
              <Select
                value={form.team_id}
                onValueChange={(v) => setForm((f) => ({ ...f, team_id: v }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_TEAM}>No team</SelectItem>
                  {teams.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {form.team_id !== NO_TEAM && (
              <div className="grid gap-2">
                <Label>Team role</Label>
                <Select
                  value={form.team_role}
                  onValueChange={(v) =>
                    setForm((f) => ({ ...f, team_role: v as FormState["team_role"] }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">Member</SelectItem>
                    <SelectItem value="admin">Admin</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
          <DialogFooter>
            <Button
              onClick={handleSaveMapping}
              disabled={saving || !form.entra_app_role.trim()}
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Add mapping"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
