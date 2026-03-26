import { useServers } from "@/hooks/use-servers";
import { useTemplates } from "@/hooks/use-templates";
import { ServerTable } from "@/components/server-table";
import { CreateServerDialog } from "@/components/create-server-dialog";

export default function App() {
  const { servers, loading, error, refresh } = useServers();
  const { templates } = useTemplates();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              MCP Platform
            </h1>
            <p className="text-sm text-muted-foreground">
              Deploy and manage MCP servers
            </p>
          </div>
          <CreateServerDialog templates={templates} onCreated={refresh} />
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        {loading ? (
          <div className="py-12 text-center text-muted-foreground">
            Loading...
          </div>
        ) : error ? (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-destructive">
            {error}
          </div>
        ) : (
          <ServerTable servers={servers} onRefresh={refresh} />
        )}
      </main>
    </div>
  );
}
