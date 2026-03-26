import { useState } from "react";
import { useServers } from "@/hooks/use-servers";
import { ServerTable } from "@/components/server-table";
import { ServerDetail } from "@/components/server-detail";
import { CreateServerDialog } from "@/components/create-server-dialog";
import { ThemeToggle } from "@/components/theme-toggle";

export default function App() {
  const { servers, loading, error, refresh } = useServers();
  const [selectedServer, setSelectedServer] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <button
            onClick={() => setSelectedServer(null)}
            className="text-left"
          >
            <h1 className="text-xl font-semibold tracking-tight">
              MCP Platform
            </h1>
            <p className="text-sm text-muted-foreground">
              Deploy and manage MCP servers
            </p>
          </button>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            {!selectedServer && (
              <CreateServerDialog onCreated={refresh} />
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        {selectedServer ? (
          <ServerDetail
            serverName={selectedServer}
            onBack={() => setSelectedServer(null)}
          />
        ) : loading ? (
          <div className="py-12 text-center text-muted-foreground">
            Loading...
          </div>
        ) : error ? (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-destructive">
            {error}
          </div>
        ) : (
          <ServerTable
            servers={servers}
            onRefresh={refresh}
            onSelect={setSelectedServer}
          />
        )}
      </main>
    </div>
  );
}
