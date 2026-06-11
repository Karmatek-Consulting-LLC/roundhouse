import { useState } from "react";
import { api, type Server } from "@/lib/api";
import { Button } from "@/components/ui/button";
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

interface ServerTableProps {
  servers: Server[];
  onRefresh: () => void;
  onSelect: (name: string) => void;
}

export function ServerTable({ servers, onRefresh, onSelect }: ServerTableProps) {
  const [busy, setBusy] = useState<string | null>(null);

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

  return (
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
          {servers.map((s) => (
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
  );
}
