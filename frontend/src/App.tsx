import { useState } from "react";
import {
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { useServers } from "@/hooks/use-servers";
import { ServerTable } from "@/components/server-table";
import { ObserveConsole } from "@/components/observe/observe-console";
import { ServerEdit } from "@/components/server-edit";
import { CreateServerPage } from "@/components/create-server-page";
import { LoginPage } from "@/components/login-page";
import { AuthCallback } from "@/components/auth-callback";
import { UserManagement } from "@/components/user-management";
import { TeamManagement } from "@/components/team-management";
import { PlatformSettings } from "@/components/platform-settings";
import { AuditLogPage } from "@/components/audit-log";
import { LogConsolePage } from "@/components/log-console";
import { BackupRestore } from "@/components/backup-restore";
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
import { ChangePasswordDialog } from "@/components/change-password-dialog";
import { DatabaseBackup, History, KeyRound, LogOut, ScrollText, Settings, Shield, Users } from "lucide-react";

export type ServersOutletContext = ReturnType<typeof useServers>;

function LoadingScreen() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <p className="text-muted-foreground">Loading...</p>
    </div>
  );
}

function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <LoadingScreen />;
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <Outlet />;
}

function LoginRoute() {
  const { user, loading } = useAuth();
  if (loading) return <LoadingScreen />;
  if (user) return <Navigate to="/" replace />;
  return <LoginPage />;
}

function SuperAdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (user?.role !== "superadmin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const serversState = useServers();
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);

  if (!user) return null;

  const isSuperAdmin = user.role === "superadmin";
  const isServerList = location.pathname === "/servers";

  function goHome() {
    navigate("/");
  }

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      isActive
        ? "bg-muted text-foreground"
        : "text-muted-foreground hover:text-foreground"
    }`;

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex w-full max-w-screen-2xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-6">
            <button
              type="button"
              onClick={goHome}
              className="flex items-center gap-3 text-left"
            >
              <Logo className="h-10 w-auto shrink-0" />
              <div>
                <h1 className="font-display text-2xl font-extrabold uppercase tracking-[0.08em]">
                  Round<span className="text-primary">house</span>
                </h1>
                <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                  Deploy &amp; manage MCP servers
                </p>
              </div>
            </button>
            <nav className="hidden items-center gap-1 sm:flex">
              <NavLink to="/" end className={navLinkClass}>
                <span className="inline-flex items-center gap-1.5">
                  Dashboard
                  <span className="h-1.5 w-1.5 rounded-full bg-primary shadow-[0_0_8px] shadow-primary/60 animate-pulse" />
                </span>
              </NavLink>
              <NavLink to="/servers" className={navLinkClass}>
                Servers
              </NavLink>
            </nav>
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            {isServerList && (
              <Button onClick={() => navigate("/servers/new")}>Create Server</Button>
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
                    <DropdownMenuItem
                      onClick={() => {
                        navigate("/users");
                      }}
                    >
                      <Shield className="mr-2 h-4 w-4" />
                      Manage Users
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        navigate("/teams");
                      }}
                    >
                      <Users className="mr-2 h-4 w-4" />
                      Manage Teams
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        navigate("/settings");
                      }}
                    >
                      <Settings className="mr-2 h-4 w-4" />
                      Platform Settings
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        navigate("/audit");
                      }}
                    >
                      <History className="mr-2 h-4 w-4" />
                      Audit Log
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        navigate("/logs");
                      }}
                    >
                      <ScrollText className="mr-2 h-4 w-4" />
                      Logs
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        navigate("/backup");
                      }}
                    >
                      <DatabaseBackup className="mr-2 h-4 w-4" />
                      Backup &amp; Restore
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                  </>
                )}
                {/* SSO (Entra) accounts are external: their password lives in
                    Entra, not Roundhouse, so there's nothing to change here. */}
                {user.auth_source !== "entra" && (
                  <DropdownMenuItem onSelect={() => setChangePasswordOpen(true)}>
                    <KeyRound className="mr-2 h-4 w-4" />
                    Change password
                  </DropdownMenuItem>
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

      <ChangePasswordDialog open={changePasswordOpen} onOpenChange={setChangePasswordOpen} />

      <main className="mx-auto w-full max-w-screen-2xl px-6 py-8">
        <Outlet context={serversState satisfies ServersOutletContext} />
      </main>
    </div>
  );
}

function DashboardRoute() {
  const { servers, loading, error } = useOutletContext<ServersOutletContext>();
  const [searchParams] = useSearchParams();
  const server = searchParams.get("server") ?? undefined;

  if (loading && servers.length === 0) {
    return <div className="py-12 text-center text-muted-foreground">Loading...</div>;
  }
  if (error) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-destructive">
        {error}
      </div>
    );
  }
  return <ObserveConsole server={server} servers={servers} showFleet />;
}

// The old /observe console folded into the Dashboard; keep the deep-link alive.
function ObserveRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/${search}`} replace />;
}

function ServersPage() {
  const navigate = useNavigate();
  const { servers, loading, error, refresh } =
    useOutletContext<ServersOutletContext>();

  if (loading) {
    return (
      <div className="py-12 text-center text-muted-foreground">Loading...</div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-destructive">
        {error}
      </div>
    );
  }
  return (
    <ServerTable
      servers={servers}
      onRefresh={refresh}
      onSelect={(name) => {
        navigate(`/servers/${encodeURIComponent(name)}`);
      }}
    />
  );
}

function ServerEditRoute() {
  const { serverName } = useParams();
  if (!serverName) return <Navigate to="/" replace />;
  return <ServerEdit serverName={serverName} />;
}

function UserManagementPage() {
  const navigate = useNavigate();
  return <UserManagement onBack={() => navigate("/")} />;
}

function TeamManagementPage() {
  const navigate = useNavigate();
  return <TeamManagement onBack={() => navigate("/")} />;
}

function PlatformSettingsPage() {
  const navigate = useNavigate();
  return <PlatformSettings onBack={() => navigate("/")} />;
}

function AuditLogRoute() {
  const navigate = useNavigate();
  return <AuditLogPage onBack={() => navigate("/")} />;
}

function LogConsoleRoute() {
  const navigate = useNavigate();
  return <LogConsolePage onBack={() => navigate("/")} />;
}

function BackupRestoreRoute() {
  const navigate = useNavigate();
  return <BackupRestore onBack={() => navigate("/")} />;
}

export default function App() {
  const { loading: authLoading } = useAuth();

  if (authLoading) {
    return <LoadingScreen />;
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<DashboardRoute />} />
          <Route path="/observe" element={<ObserveRedirect />} />
          <Route path="/servers" element={<ServersPage />} />
          <Route path="/servers/new" element={<CreateServerPage />} />
          <Route path="/servers/:serverName/*" element={<ServerEditRoute />} />
          <Route
            path="/users"
            element={
              <SuperAdminOnly>
                <UserManagementPage />
              </SuperAdminOnly>
            }
          />
          <Route path="/teams" element={<TeamManagementPage />} />
          <Route
            path="/settings"
            element={
              <SuperAdminOnly>
                <PlatformSettingsPage />
              </SuperAdminOnly>
            }
          />
          <Route
            path="/audit"
            element={
              <SuperAdminOnly>
                <AuditLogRoute />
              </SuperAdminOnly>
            }
          />
          <Route
            path="/logs"
            element={
              <SuperAdminOnly>
                <LogConsoleRoute />
              </SuperAdminOnly>
            }
          />
          <Route
            path="/backup"
            element={
              <SuperAdminOnly>
                <BackupRestoreRoute />
              </SuperAdminOnly>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}
