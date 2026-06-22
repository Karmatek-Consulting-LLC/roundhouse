import { useMemo, useState } from "react";
import { api, type Server } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { StatusBadge } from "@/components/status-badge";
import { TurntableEmpty } from "@/components/turntable-empty";
import { Search, X } from "lucide-react";

interface ServerTableProps {
  servers: Server[];
  onRefresh: () => void;
  onSelect: (name: string) => void;
}

const ALL_OWNERS = "__all__";

export function ServerTable({ servers, onRefresh, onSelect }: ServerTableProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [owner, setOwner] = useState<string>(ALL_OWNERS);

  // Distinct owners for the dropdown, sorted; only worth showing when >1.
  const owners = useMemo(() => {
    const set = new Set<string>();
    for (const s of servers) set.add(s.owner_email ?? "—");
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [servers]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return servers.filter((s) => {
      const ownerEmail = s.owner_email ?? "—";
      if (owner !== ALL_OWNERS && ownerEmail !== owner) return false;
      if (q && !s.name.toLowerCase().includes(q) && !ownerEmail.toLowerCase().includes(q))
        return false;
      return true;
    });
  }, [servers, query, owner]);

  async function handleAction(name: string, action: () => Promise<unknown>) {
    setBusy(name);
    try {
      await action();
      onRefresh();
    } finally {
      setBusy(null);
    }
  }

  if (servers.length === 0) {
    return (
      <TurntableEmpty title="No engines in the house">
        Create a server to give your first tool a permanent stall.
      </TurntableEmpty>
    );
  }

  const hasFilter = query.trim() !== "" || owner !== ALL_OWNERS;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[16rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by server name or owner…"
            className="pl-8"
          />
        </div>
        {owners.length > 1 && (
          <Select value={owner} onValueChange={setOwner}>
            <SelectTrigger className="w-[14rem]">
              <SelectValue placeholder="All owners" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_OWNERS}>All owners</SelectItem>
              {owners.map((o) => (
                <SelectItem key={o} value={o}>
                  {o}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {hasFilter && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setQuery("");
              setOwner(ALL_OWNERS);
            }}
          >
            <X className="mr-1 h-3.5 w-3.5" /> Clear
          </Button>
        )}
        <span className="ml-auto font-mono text-xs text-muted-foreground tabular-nums">
          {hasFilter ? `${filtered.length} of ${servers.length}` : `${servers.length}`} server
          {servers.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="w-full min-w-0 rounded-lg border">
        <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Primitives</TableHead>
            <TableHead>Replicas</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="min-w-[14rem]">Endpoint</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filtered.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="py-10 text-center text-sm text-muted-foreground">
                No servers match this filter.
              </TableCell>
            </TableRow>
          )}
          {filtered.map((s) => (
            <TableRow key={s.name}>
              <TableCell>
                <button
                  className="font-mono text-[13px] font-medium text-primary hover:underline"
                  onClick={() => onSelect(s.name)}
                >
                  {s.name}
                </button>
              </TableCell>
              <TableCell className="text-muted-foreground text-sm">
                {s.owner_email ?? "\u2014"}
              </TableCell>
              <TableCell className="text-muted-foreground text-sm">
                {s.primitives?.length ?? 0} primitives
              </TableCell>
              <TableCell className="text-muted-foreground text-sm tabular-nums">
                {s.status === "not_deployed" || s.status === "unknown"
                  ? "-"
                  : s.docker_swarm_mode
                    ? `${s.replicas_running}/${s.replicas_desired}`
                    : s.replicas_running > 0
                      ? "1"
                      : "0"}
              </TableCell>
              <TableCell>
                <StatusBadge status={s.status} />
              </TableCell>
              <TableCell className="min-w-0">
                <code className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                  {s.url}
                </code>
              </TableCell>
              <TableCell className="text-right">
                <div className="flex items-center justify-end gap-2">
                  {s.status === "not_deployed" || s.status === "unknown" ? (
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {s.status === "unknown" ? "Check Docker" : "Deploy in details"}
                    </span>
                  ) : s.status === "running" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy === s.name}
                      onClick={() =>
                        handleAction(s.name, () => api.stopServer(s.name))
                      }
                    >
                      Stop
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy === s.name}
                      onClick={() =>
                        handleAction(s.name, () => api.startServer(s.name))
                      }
                    >
                      Start
                    </Button>
                  )}
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={busy === s.name}
                    onClick={() =>
                      handleAction(s.name, () => api.deleteServer(s.name))
                    }
                  >
                    Delete
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
        </Table>
      </div>
    </div>
  );
}
