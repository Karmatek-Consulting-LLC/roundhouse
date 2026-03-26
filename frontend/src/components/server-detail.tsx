import { useCallback, useEffect, useState } from "react";
import { api, type Primitive, type Server } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { AddPrimitiveDialog } from "@/components/add-primitive-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PackageManager } from "@/components/package-manager";
import { EnvVarsEditor } from "@/components/env-vars-editor";
import { ArrowLeft, Trash2 } from "lucide-react";

interface ServerDetailProps {
  serverName: string;
  onBack: () => void;
}

const kindLabels: Record<string, string> = {
  tool: "Tool",
  resource: "Resource",
  resource_template: "Resource Template",
  prompt: "Prompt",
};

const kindColors: Record<string, string> = {
  tool: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  resource: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
  resource_template: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400",
  prompt: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
};

export function ServerDetail({ serverName, onBack }: ServerDetailProps) {
  const [server, setServer] = useState<Server | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getServer(serverName);
      setServer(data);
    } finally {
      setLoading(false);
    }
  }, [serverName]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleDeletePrimitive(p: Primitive) {
    setDeleting(p.name);
    try {
      await api.deletePrimitive(serverName, p.name);
      refresh();
    } finally {
      setDeleting(null);
    }
  }

  if (loading) {
    return <div className="py-12 text-center text-muted-foreground">Loading...</div>;
  }

  if (!server) {
    return <div className="py-12 text-center text-muted-foreground">Server not found</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back
        </Button>
      </div>

      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-semibold tracking-tight">{server.name}</h2>
            <StatusBadge status={server.status} />
          </div>
          {server.description && (
            <p className="mt-1 text-sm text-muted-foreground">{server.description}</p>
          )}
          <p className="mt-2">
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
              {server.url}
            </code>
          </p>
        </div>
        <AddPrimitiveDialog serverName={serverName} onAdded={refresh} />
      </div>

      {server.primitives.length === 0 ? (
        <div className="rounded-lg border border-dashed p-12 text-center text-muted-foreground">
          No primitives yet. Click "Add Primitive" to define tools, resources, or prompts.
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Details</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {server.primitives.map((p) => (
                <TableRow key={`${p.kind}-${p.name}`}>
                  <TableCell className="font-medium font-mono text-sm">
                    {p.name}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={kindColors[p.kind] ?? ""}>
                      {kindLabels[p.kind] ?? p.kind}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {p.description || "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {"parameters" in p && p.parameters.length > 0 && (
                      <span>
                        {p.parameters.map((pr) => pr.name).join(", ")}
                      </span>
                    )}
                    {"uri" in p && <code className="rounded bg-muted px-1">{p.uri}</code>}
                    {"uri_template" in p && (
                      <code className="rounded bg-muted px-1">{p.uri_template}</code>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <AddPrimitiveDialog
                        serverName={serverName}
                        onAdded={refresh}
                        existing={p}
                      />
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={deleting === p.name}
                        onClick={() => handleDeletePrimitive(p)}
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

      <div className="grid gap-6 md:grid-cols-2">
        <div className="rounded-lg border p-4">
          <PackageManager
            serverName={serverName}
            packages={server.pip_packages}
            onUpdated={refresh}
          />
        </div>
        <div className="rounded-lg border p-4">
          <EnvVarsEditor
            serverName={serverName}
            envVars={server.env_vars}
            onUpdated={refresh}
          />
        </div>
      </div>
    </div>
  );
}
