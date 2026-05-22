import { useState } from "react";
import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { useServers } from "@/hooks/use-servers";
import { ServerTable } from "@/components/server-table";
import { ServerEdit } from "@/components/server-edit";
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
import { ChangePasswordDialog } from "@/components/change-password-dialog";
import { KeyRound, LogOut, Settings, Shield, Users } from "lucide-react";

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
  const isServerList = location.pathname === "/";

  function goHome() {
    navigate("/");
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex w-full max-w-screen-2xl items-center justify-between px-6 py-4">
          <button
            type="button"
            onClick={goHome}
            className="flex items-center gap-3 text-left"
          >
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
            {isServerList && <CreateServerDialog onCreated={serversState.refresh} />}
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
                    <DropdownMenuSeparator />
                  </>
                )}
                <DropdownMenuItem onSelect={() => setChangePasswordOpen(true)}>
                  <KeyRound className="mr-2 h-4 w-4" />
                  Change password
                </DropdownMenuItem>
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

function HomePage() {
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

export default function App() {
  const { loading: authLoading } = useAuth();

  if (authLoading) {
    return <LoadingScreen />;
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<HomePage />} />
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}
