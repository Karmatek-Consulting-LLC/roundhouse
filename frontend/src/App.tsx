import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useServers } from "@/hooks/use-servers";
import { ServerTable } from "@/components/server-table";
import { ServerDetail } from "@/components/server-detail";
import { CreateServerDialog } from "@/components/create-server-dialog";
import { LoginPage } from "@/components/login-page";
import { UserManagement } from "@/components/user-management";
import { TeamManagement } from "@/components/team-management";
import { PlatformSettings } from "@/components/platform-settings";
import { ThemeToggle } from "@/components/theme-toggle";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LogOut, Settings, Shield, Users } from "lucide-react";

type View = "servers" | "users" | "teams" | "settings";

export default function App() {
  const { user, loading: authLoading, logout } = useAuth();
  const { servers, loading, error, refresh } = useServers();
  const [selectedServer, setSelectedServer] = useState<string | null>(null);
  const [view, setView] = useState<View>("servers");

  if (authLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  const isSuperAdmin = user.role === "superadmin";

  function goHome() {
    setSelectedServer(null);
    setView("servers");
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <button onClick={goHome} className="flex items-center gap-3 text-left">
            <Logo className="h-10 w-10 shrink-0" />
            <div>
              <h1 className="text-xl font-semibold tracking-tight">
                MCP Platform
              </h1>
              <p className="text-sm text-muted-foreground">
                Deploy and manage MCP servers
              </p>
            </div>
          </button>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            {view === "servers" && !selectedServer && (
              <CreateServerDialog onCreated={refresh} />
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  {user.display_name}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {isSuperAdmin && (
                  <>
                    <DropdownMenuItem onClick={() => { setView("users"); setSelectedServer(null); }}>
                      <Shield className="mr-2 h-4 w-4" />
                      Manage Users
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => { setView("teams"); setSelectedServer(null); }}>
                      <Users className="mr-2 h-4 w-4" />
                      Manage Teams
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => { setView("settings"); setSelectedServer(null); }}>
                      <Settings className="mr-2 h-4 w-4" />
                      Platform Settings
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                  </>
                )}
                <DropdownMenuItem onClick={logout}>
                  <LogOut className="mr-2 h-4 w-4" />
                  Sign Out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        {view === "settings" && isSuperAdmin ? (
          <PlatformSettings onBack={goHome} />
        ) : view === "users" && isSuperAdmin ? (
          <UserManagement onBack={goHome} />
        ) : view === "teams" ? (
          <TeamManagement onBack={goHome} />
        ) : selectedServer ? (
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
