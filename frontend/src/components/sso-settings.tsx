import { useCallback, useEffect, useState } from "react";
import { api, type RoleMapping, type Team } from "@/lib/api";
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
import { Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";

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
 * Manage the Entra app role -> Roundhouse grant mappings (the table the SSO
 * login flow reads on every sign-in). Self-contained card for the Platform
 * Settings page; superadmin-gated by the route that renders Settings.
 */
export function SsoMappingsCard() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [mappings, setMappings] = useState<RoleMapping[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<RoleMapping | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [status, rows, teamList] = await Promise.all([
        api.oidcStatus().catch(() => ({ enabled: false })),
        api.listRoleMappings(),
        api.listTeams().catch(() => [] as Team[]),
      ]);
      setEnabled(status.enabled);
      setMappings(rows);
      setTeams(teamList);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

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

  async function handleSave() {
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

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5" />
          Entra ID SSO
          {enabled !== null && (
            <Badge variant={enabled ? "default" : "secondary"}>
              {enabled ? "Enabled" : "Not configured"}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          Map Entra <strong>app roles</strong> (from the token's{" "}
          <code className="rounded bg-muted px-1">roles</code> claim) to a
          Roundhouse role and optional team. These are applied on every SSO
          sign-in; Entra is authoritative for SSO users. A user whose roles match
          no mapping signs in with the lowest privilege (<code className="rounded bg-muted px-1">user</code>).
          {enabled === false && (
            <>
              {" "}Mappings can be edited now and take effect once the{" "}
              <code className="rounded bg-muted px-1">ENTRA_*</code> environment
              variables are set.
            </>
          )}
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
              onClick={handleSave}
              disabled={saving || !form.entra_app_role.trim()}
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Add mapping"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
