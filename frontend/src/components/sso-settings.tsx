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

type TeamForm = {
  entra_app_role: string;
  team_id: string;
  team_role: "admin" | "member";
};

const parseRoles = (s: string): string[] =>
  s.split(",").map((x) => x.trim()).filter(Boolean);

const joinRoles = (rows: RoleMapping[], role: "superadmin" | "user"): string =>
  rows
    .filter((m) => m.team_id === null && m.roundhouse_role === role)
    .map((m) => m.entra_app_role)
    .join(", ");

/**
 * Platform Settings → Entra ID SSO. Cards:
 *  - Connection: OIDC connection settings (tenant/client/secret), redirect URI
 *    read-only, plus the link-local toggle.
 *  - Built-in roles: which Entra app roles grant Super Admin / User.
 *  - Team access: Entra app role → team membership grants.
 */
export function SsoSettingsCard() {
  const [config, setConfig] = useState<SsoConfig | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);

  // Connection form
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const [linkLocal, setLinkLocal] = useState(false);
  const [savingConn, setSavingConn] = useState(false);
  const [connError, setConnError] = useState<string | null>(null);

  // Built-in role mappings (comma-separated Entra app roles per built-in role)
  const [superAdminRoles, setSuperAdminRoles] = useState("");
  const [userRoles, setUserRoles] = useState("");
  const [savingRoles, setSavingRoles] = useState(false);
  const [rolesError, setRolesError] = useState<string | null>(null);

  // Team mappings (team_id != null rows)
  const [teamMappings, setTeamMappings] = useState<RoleMapping[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<RoleMapping | null>(null);
  const [teamForm, setTeamForm] = useState<TeamForm>({
    entra_app_role: "",
    team_id: "",
    team_role: "member",
  });
  const [savingTeam, setSavingTeam] = useState(false);
  const [teamError, setTeamError] = useState<string | null>(null);

  const applyConfig = useCallback((cfg: SsoConfig) => {
    setConfig(cfg);
    setTenantId(cfg.entra_tenant_id);
    setClientId(cfg.entra_client_id);
    setLinkLocal(cfg.link_local_by_email);
    setSecret("");
    setClearSecret(false);
  }, []);

  // Split the flat mapping list into the two UI sections.
  const applyMappings = useCallback((rows: RoleMapping[], seedBuiltin: boolean) => {
    setTeamMappings(rows.filter((m) => m.team_id !== null));
    if (seedBuiltin) {
      setSuperAdminRoles(joinRoles(rows, "superadmin"));
      setUserRoles(joinRoles(rows, "user"));
    }
  }, []);

  const loadAll = useCallback(async () => {
    const [cfg, rows, teamList] = await Promise.all([
      api.getSsoConfig(),
      api.listRoleMappings(),
      api.listTeams().catch(() => [] as Team[]),
    ]);
    applyConfig(cfg);
    setTeams(teamList);
    applyMappings(rows, true);
  }, [applyConfig, applyMappings]);

  useEffect(() => {
    loadAll().finally(() => setLoading(false));
  }, [loadAll]);

  // Refresh only the team-mapping table (don't clobber unsaved built-in edits).
  const reloadTeamMappings = useCallback(async () => {
    applyMappings(await api.listRoleMappings(), false);
  }, [applyMappings]);

  async function handleSaveConnection() {
    setConnError(null);
    setSavingConn(true);
    try {
      const body: {
        entra_tenant_id: string;
        entra_client_id: string;
        entra_client_secret?: string;
        link_local_by_email: boolean;
      } = {
        entra_tenant_id: tenantId.trim(),
        entra_client_id: clientId.trim(),
        link_local_by_email: linkLocal,
      };
      if (secret.length > 0) body.entra_client_secret = secret;
      else if (clearSecret) body.entra_client_secret = "";
      applyConfig(await api.updateSsoConfig(body));
    } catch (e) {
      setConnError(e instanceof Error ? e.message : "Failed to save SSO settings");
    } finally {
      setSavingConn(false);
    }
  }

  async function handleSaveRoles() {
    setRolesError(null);
    setSavingRoles(true);
    try {
      const rows = await api.updateBuiltinRoleMappings({
        superadmin: parseRoles(superAdminRoles),
        user: parseRoles(userRoles),
      });
      applyMappings(rows, true);
    } catch (e) {
      setRolesError(e instanceof Error ? e.message : "Failed to save role mappings");
    } finally {
      setSavingRoles(false);
    }
  }

  function openCreateTeam() {
    setEditing(null);
    setTeamForm({ entra_app_role: "", team_id: teams[0]?.id ?? "", team_role: "member" });
    setTeamError(null);
    setDialogOpen(true);
  }

  function openEditTeam(m: RoleMapping) {
    setEditing(m);
    setTeamForm({
      entra_app_role: m.entra_app_role,
      team_id: m.team_id ?? "",
      team_role: m.team_role,
    });
    setTeamError(null);
    setDialogOpen(true);
  }

  async function handleSaveTeamMapping() {
    setTeamError(null);
    setSavingTeam(true);
    try {
      const body = {
        entra_app_role: teamForm.entra_app_role.trim(),
        roundhouse_role: "user" as const, // team grants don't set a top-level role
        team_id: teamForm.team_id,
        team_role: teamForm.team_role,
      };
      if (editing) await api.updateRoleMapping(editing.id, body);
      else await api.createRoleMapping(body);
      setDialogOpen(false);
      reloadTeamMappings();
    } catch (e) {
      setTeamError(e instanceof Error ? e.message : "Failed to save team mapping");
    } finally {
      setSavingTeam(false);
    }
  }

  async function handleDeleteTeam(m: RoleMapping) {
    if (!confirm(`Delete the team mapping for "${m.entra_app_role}"?`)) return;
    await api.deleteRoleMapping(m.id);
    reloadTeamMappings();
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
            encrypted at rest. SSO turns on once all values are set.
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
                <Label>Redirect URI</Label>
                <code className="block rounded bg-muted px-2 py-1.5 text-sm font-mono break-all">
                  {config?.entra_redirect_uri}
                </code>
                <p className="text-xs text-muted-foreground">
                  Read-only — derived from your public URL (PUBLIC_HOSTNAME).
                  Register this exact value as a redirect URI on the Entra app.
                </p>
                {config?.entra_redirect_uri.includes("localhost") && (
                  <p className="text-xs text-amber-700 dark:text-amber-400">
                    This points at localhost. Deploy with a real{" "}
                    <code className="rounded bg-muted px-1">PUBLIC_HOSTNAME</code> so
                    the redirect matches your public URL.
                  </p>
                )}
              </div>
              <div className="grid gap-1.5">
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-primary"
                    checked={linkLocal}
                    onChange={(e) => setLinkLocal(e.target.checked)}
                  />
                  <span>Link existing local accounts on first SSO login</span>
                </label>
                <p className="text-xs text-muted-foreground pl-6">
                  When a user signs in with Microsoft and their email matches an
                  existing local account, adopt that account (keeping its teams,
                  server ownership, and password as a break-glass fallback) instead
                  of refusing. Match is by email — safe in a single tenant.
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
          <CardTitle>Built-in roles</CardTitle>
          <CardDescription>
            Enter the Entra <strong>app roles</strong> (from the token's{" "}
            <code className="rounded bg-muted px-1">roles</code> claim) that grant
            each Roundhouse role. Comma-separate to map several. A user whose roles
            match none signs in as <code className="rounded bg-muted px-1">User</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2">
            <Label htmlFor="role-superadmin">
              Super Admin <span className="text-muted-foreground">— full platform control</span>
            </Label>
            <Input
              id="role-superadmin"
              value={superAdminRoles}
              onChange={(e) => setSuperAdminRoles(e.target.value)}
              placeholder="Roundhouse.Admins, Platform.Owners"
              className="font-mono text-sm"
              autoComplete="off"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="role-user">
              User <span className="text-muted-foreground">— own + teammates' servers</span>
            </Label>
            <Input
              id="role-user"
              value={userRoles}
              onChange={(e) => setUserRoles(e.target.value)}
              placeholder="Roundhouse.Users"
              className="font-mono text-sm"
              autoComplete="off"
            />
            <p className="text-xs text-muted-foreground">
              Optional — listing roles here is only needed if you want to restrict
              sign-in later; unmatched users already default to User.
            </p>
          </div>
          {rolesError && <p className="text-sm text-destructive">{rolesError}</p>}
          <Button size="sm" onClick={handleSaveRoles} disabled={savingRoles}>
            <Save className="mr-1 h-4 w-4" />
            {savingRoles ? "Saving…" : "Save roles"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Team access</CardTitle>
          <CardDescription>
            Optionally grant team membership from an Entra app role. Each maps an
            app role to a team and a team role (member or admin).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex justify-end">
            <Button size="sm" onClick={openCreateTeam} disabled={teams.length === 0}>
              <Plus className="mr-1 h-4 w-4" />
              Add team mapping
            </Button>
          </div>

          {teams.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No teams exist yet. Create a team first to map app roles to it.
            </div>
          ) : teamMappings.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No team mappings yet.
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Entra app role</TableHead>
                    <TableHead>Team</TableHead>
                    <TableHead>Team role</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {teamMappings.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="font-mono text-sm">
                        {m.entra_app_role}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {teamName(m.team_id)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {m.team_role}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            title="Edit mapping"
                            aria-label={`Edit team mapping ${m.entra_app_role}`}
                            onClick={() => openEditTeam(m)}
                          >
                            <Pencil className="h-3 w-3" />
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            title="Delete mapping"
                            aria-label={`Delete team mapping ${m.entra_app_role}`}
                            onClick={() => handleDeleteTeam(m)}
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
            <DialogTitle>
              {editing ? "Edit team mapping" : "Add team mapping"}
            </DialogTitle>
            <DialogDescription>
              Grant a team membership from an Entra app role.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label>Entra app role</Label>
              <Input
                value={teamForm.entra_app_role}
                onChange={(e) =>
                  setTeamForm((f) => ({ ...f, entra_app_role: e.target.value }))
                }
                placeholder="Roundhouse.TeamA"
                className="font-mono text-sm"
              />
            </div>
            <div className="grid gap-2">
              <Label>Team</Label>
              <Select
                value={teamForm.team_id}
                onValueChange={(v) => setTeamForm((f) => ({ ...f, team_id: v }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {teams.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Team role</Label>
              <Select
                value={teamForm.team_role}
                onValueChange={(v) =>
                  setTeamForm((f) => ({ ...f, team_role: v as TeamForm["team_role"] }))
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
            {teamError && <p className="text-sm text-destructive">{teamError}</p>}
          </div>
          <DialogFooter>
            <Button
              onClick={handleSaveTeamMapping}
              disabled={savingTeam || !teamForm.entra_app_role.trim() || !teamForm.team_id}
            >
              {savingTeam ? "Saving…" : editing ? "Save changes" : "Add mapping"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
