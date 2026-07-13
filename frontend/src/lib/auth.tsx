import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, AUTH_EXPIRED_EVENT } from "@/lib/api";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: "superadmin" | "user";
  /** "local" (password / break-glass) or "entra" (SSO). Absent on older API. */
  auth_source?: "local" | "entra";
}

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** Install a token obtained out-of-band (e.g. the SSO callback). The `me`
   * effect then loads the user, same as a password login. */
  applyToken: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem("token")
  );
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then((u) => setUser(u as AuthUser))
      .catch(() => {
        localStorage.removeItem("token");
        setToken(null);
      })
      .finally(() => setLoading(false));
  }, [token]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.login(email, password);
    localStorage.setItem("token", res.access_token);
    setToken(res.access_token);
    setUser(res.user as AuthUser);
  }, []);

  const applyToken = useCallback((newToken: string) => {
    localStorage.setItem("token", newToken);
    setLoading(true);
    setToken(newToken);
  }, []);

  const logout = useCallback(() => {
    // Record the sign-out in the auth log before dropping the token. Fire and
    // forget: logout must succeed locally even if the API is unreachable.
    api.logout().catch(() => {});
    localStorage.removeItem("token");
    setToken(null);
    setUser(null);
  }, []);

  // When any API call sees a 401, the api layer dispatches AUTH_EXPIRED_EVENT.
  // Clear React state here so RequireAuth flips to <Navigate to="/login" />.
  useEffect(() => {
    const onExpired = () => {
      setToken(null);
      setUser(null);
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, applyToken, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
