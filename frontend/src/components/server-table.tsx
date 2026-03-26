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
      <div className="rounded-lg border border-dashed p-12 text-center text-muted-foreground">
        No MCP servers running. Create one to get started.
      </div>
    );
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Primitives</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Endpoint</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {servers.map((s) => (
            <TableRow key={s.name}>
              <TableCell>
                <button
                  className="font-medium text-primary hover:underline"
                  onClick={() => onSelect(s.name)}
                >
                  {s.name}
                </button>
              </TableCell>
              <TableCell className="text-muted-foreground text-sm">
                {s.primitives?.length ?? 0} primitives
              </TableCell>
              <TableCell>
                <StatusBadge status={s.status} />
              </TableCell>
              <TableCell>
                <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                  {s.url}
                </code>
              </TableCell>
              <TableCell className="text-right">
                <div className="flex items-center justify-end gap-2">
                  {s.status === "running" ? (
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
