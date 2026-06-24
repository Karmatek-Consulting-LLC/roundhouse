import { useCallback, useEffect, useState } from "react";
import { api, type AuthUser } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { SetUserPasswordDialog } from "@/components/change-password-dialog";
import { ArrowLeft, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";

interface UserManagementProps {
  onBack: () => void;
}

export function UserManagement({ onBack }: UserManagementProps) {
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [passwordTarget, setPasswordTarget] = useState<AuthUser | null>(null);
  const [editTarget, setEditTarget] = useState<AuthUser | null>(null);

  const refresh = useCallback(async () => {
    try {
      setUsers(await api.listUsers());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleCreate() {
    setError(null);
    setCreating(true);
    try {
      await api.register({ email, password, display_name: displayName, role });
      setDialogOpen(false);
      setEmail("");
      setDisplayName("");
      setPassword("");
      setRole("user");
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create user");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    await api.deleteUser(id);
    refresh();
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back
        </Button>
      </div>

      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">Users</h2>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="mr-1 h-4 w-4" />
              Create User
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Create User</DialogTitle>
              <DialogDescription>Add a new user to the platform.</DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <div className="grid gap-2">
                <Label>Email</Label>
                <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@example.com" />
              </div>
              <div className="grid gap-2">
                <Label>Display Name</Label>
                <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Jane Doe" />
              </div>
              <div className="grid gap-2">
                <Label>Password</Label>
                <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              <div className="grid gap-2">
                <Label>Role</Label>
                <Select value={role} onValueChange={setRole}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">User</SelectItem>
                    <SelectItem value="superadmin">SuperAdmin</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
            </div>
            <DialogFooter>
              <Button onClick={handleCreate} disabled={!email || !password || !displayName || creating}>
                {creating ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {loading ? (
        <div className="py-12 text-center text-muted-foreground">Loading...</div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-medium">{u.display_name}</TableCell>
                  <TableCell className="text-muted-foreground">{u.email}</TableCell>
                  <TableCell>
                    <Badge variant="outline">
                      {u.auth_source === "entra" ? "SSO" : "Local"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={u.role === "superadmin" ? "default" : "secondary"}>
                      {u.role}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        title="Edit user"
                        aria-label={`Edit ${u.email}`}
                        onClick={() => setEditTarget(u)}
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                      {/* SSO users are external — their password lives in Entra,
                          so the set-password action only applies to local users. */}
                      {u.auth_source !== "entra" && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          title="Set password"
                          aria-label={`Set password for ${u.email}`}
                          onClick={() => setPasswordTarget(u)}
                        >
                          <KeyRound className="h-3 w-3" />
                        </Button>
                      )}
                      <Button
                        variant="destructive"
                        size="sm"
                        title="Delete user"
                        aria-label={`Delete ${u.email}`}
                        onClick={() => handleDelete(u.id)}
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

      {passwordTarget ? (
        <SetUserPasswordDialog
          key={passwordTarget.id}
          userId={passwordTarget.id}
          label={passwordTarget.email}
          open
          onOpenChange={(open) => {
            if (!open) setPasswordTarget(null);
          }}
        />
      ) : null}

      {editTarget ? (
        <EditUserDialog
          key={editTarget.id}
          user={editTarget}
          open
          onOpenChange={(open) => {
            if (!open) setEditTarget(null);
          }}
          onSaved={refresh}
        />
      ) : null}
    </div>
  );
}

interface EditUserDialogProps {
  user: AuthUser;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

function EditUserDialog({ user, open, onOpenChange, onSaved }: EditUserDialogProps) {
  const [authSource, setAuthSource] = useState<"local" | "entra">(
    user.auth_source ?? "local",
  );
  const [role, setRole] = useState<"user" | "superadmin">(
    user.role === "superadmin" ? "superadmin" : "user",
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Roles for SSO users are driven by Entra group mappings, so we only send a
  // manual role when the account is (or is becoming) local.
  const isLocal = authSource === "local";
  const convertingToLocal = user.auth_source === "entra" && authSource === "local";

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      await api.updateUser(user.id, {
        auth_source: authSource,
        ...(isLocal ? { role } : {}),
      });
      onSaved();
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update user");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit User</DialogTitle>
          <DialogDescription>{user.email}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label>Account type</Label>
            <Select
              value={authSource}
              onValueChange={(v) => setAuthSource(v as "local" | "entra")}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="entra">External (SSO)</SelectItem>
                <SelectItem value="local">Local</SelectItem>
              </SelectContent>
            </Select>
            {convertingToLocal && (
              <p className="text-xs text-muted-foreground">
                Break-glass: lets this user sign in locally with a password while
                Entra is unavailable. Set a password afterward. They return to SSO
                automatically the next time they sign in with Entra — no need to
                switch them back.
              </p>
            )}
          </div>
          <div className="grid gap-2">
            <Label>Role</Label>
            <Select
              value={role}
              onValueChange={(v) => setRole(v as "user" | "superadmin")}
              disabled={!isLocal}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="user">User</SelectItem>
                <SelectItem value="superadmin">SuperAdmin</SelectItem>
              </SelectContent>
            </Select>
            {!isLocal && (
              <p className="text-xs text-muted-foreground">
                Roles for SSO users are managed by Entra group mappings.
              </p>
            )}
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
