import { useCallback, useEffect, useState } from "react";
import { api, type AuthUser, type Team } from "@/lib/api";
import { useAuth } from "@/lib/auth";
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
import { ArrowLeft, Plus, Trash2, Users } from "lucide-react";

interface TeamManagementProps {
  onBack: () => void;
}

export function TeamManagement({ onBack }: TeamManagementProps) {
  const { user: currentUser } = useAuth();
  const isSuperAdmin = currentUser?.role === "superadmin";
  const [teams, setTeams] = useState<Team[]>([]);
  const [allUsers, setAllUsers] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);

  // Create team dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [teamName, setTeamName] = useState("");
  const [teamDesc, setTeamDesc] = useState("");
  const [creating, setCreating] = useState(false);

  // Add member dialog
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [memberRole, setMemberRole] = useState("member");

  const refresh = useCallback(async () => {
    try {
      const [t, u] = await Promise.all([
        api.listTeams(),
        isSuperAdmin ? api.listUsers() : Promise.resolve([]),
      ]);
      setTeams(t);
      setAllUsers(u);
      if (selectedTeam) {
        const updated = t.find((team) => team.id === selectedTeam.id);
        setSelectedTeam(updated ?? null);
      }
    } finally {
      setLoading(false);
    }
  }, [isSuperAdmin, selectedTeam]);

  useEffect(() => {
    refresh();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  async function handleCreateTeam() {
    setCreating(true);
    try {
      await api.createTeam({ name: teamName, description: teamDesc });
      setCreateOpen(false);
      setTeamName("");
      setTeamDesc("");
      refresh();
    } finally {
      setCreating(false);
    }
  }

  async function handleDeleteTeam(id: string) {
    await api.deleteTeam(id);
    if (selectedTeam?.id === id) setSelectedTeam(null);
    refresh();
  }

  async function handleAddMember() {
    if (!selectedTeam) return;
    await api.addTeamMember(selectedTeam.id, selectedUserId, memberRole);
    setAddMemberOpen(false);
    setSelectedUserId("");
    setMemberRole("member");
    refresh();
  }

  async function handleRemoveMember(userId: string) {
    if (!selectedTeam) return;
    await api.removeTeamMember(selectedTeam.id, userId);
    refresh();
  }

  if (selectedTeam) {
    const memberIds = new Set(selectedTeam.members.map((m) => m.user_id));
    const availableUsers = allUsers.filter((u) => !memberIds.has(u.id));
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => setSelectedTeam(null)}>
            <ArrowLeft className="mr-1 h-4 w-4" />
            Back to Teams
          </Button>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">{selectedTeam.name}</h2>
            {selectedTeam.description && (
              <p className="text-sm text-muted-foreground mt-1">{selectedTeam.description}</p>
            )}
          </div>
          {isSuperAdmin && availableUsers.length > 0 && (
            <Dialog open={addMemberOpen} onOpenChange={setAddMemberOpen}>
              <DialogTrigger asChild>
                <Button size="sm">
                  <Plus className="mr-1 h-4 w-4" />
                  Add Member
                </Button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-md">
                <DialogHeader>
                  <DialogTitle>Add Team Member</DialogTitle>
                  <DialogDescription>Add a user to {selectedTeam.name}.</DialogDescription>
                </DialogHeader>
                <div className="grid gap-4 py-4">
                  <div className="grid gap-2">
                    <Label>User</Label>
                    <Select value={selectedUserId} onValueChange={setSelectedUserId}>
                      <SelectTrigger><SelectValue placeholder="Select a user" /></SelectTrigger>
                      <SelectContent>
                        {availableUsers.map((u) => (
                          <SelectItem key={u.id} value={u.id}>
                            {u.display_name} ({u.email})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-2">
                    <Label>Role</Label>
                    <Select value={memberRole} onValueChange={setMemberRole}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="member">Member</SelectItem>
                        <SelectItem value="admin">Team Admin</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <DialogFooter>
                  <Button onClick={handleAddMember} disabled={!selectedUserId}>Add</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          )}
        </div>

        {selectedTeam.members.length === 0 ? (
          <div className="rounded-lg border border-dashed p-12 text-center text-muted-foreground">
            No members yet.
          </div>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {selectedTeam.members.map((m) => (
                  <TableRow key={m.user_id}>
                    <TableCell className="font-medium">{m.display_name}</TableCell>
                    <TableCell className="text-muted-foreground">{m.email}</TableCell>
                    <TableCell>
                      <Badge variant={m.role === "admin" ? "default" : "secondary"}>
                        {m.role}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => handleRemoveMember(m.user_id)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    );
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
        <h2 className="text-2xl font-semibold tracking-tight">Teams</h2>
        {isSuperAdmin && (
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="mr-1 h-4 w-4" />
                Create Team
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Create Team</DialogTitle>
                <DialogDescription>Create a new team for organizing MCP servers.</DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 py-4">
                <div className="grid gap-2">
                  <Label>Name</Label>
                  <Input value={teamName} onChange={(e) => setTeamName(e.target.value)} placeholder="Network Engineering" />
                </div>
                <div className="grid gap-2">
                  <Label>Description</Label>
                  <Input value={teamDesc} onChange={(e) => setTeamDesc(e.target.value)} placeholder="Optional description" />
                </div>
              </div>
              <DialogFooter>
                <Button onClick={handleCreateTeam} disabled={!teamName || creating}>
                  {creating ? "Creating..." : "Create"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>

      {loading ? (
        <div className="py-12 text-center text-muted-foreground">Loading...</div>
      ) : teams.length === 0 ? (
        <div className="rounded-lg border border-dashed p-12 text-center text-muted-foreground">
          No teams yet.
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Members</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {teams.map((t) => (
                <TableRow key={t.id}>
                  <TableCell>
                    <button
                      className="font-medium text-primary hover:underline"
                      onClick={() => setSelectedTeam(t)}
                    >
                      {t.name}
                    </button>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {t.description || "\u2014"}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1 text-muted-foreground">
                      <Users className="h-3.5 w-3.5" />
                      <span className="text-sm">{t.members.length}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    {isSuperAdmin && (
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => handleDeleteTeam(t.id)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
