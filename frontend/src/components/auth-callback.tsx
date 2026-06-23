import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/lib/auth";

/**
 * Landing route for the SSO redirect. The API callback sends the browser here
 * with the freshly-minted personal access token in the URL fragment
 * (`#token=...`) — fragments are never sent to a server, so the token stays out
 * of access logs and Referer headers. We install it into the auth context and
 * navigate into the app; AuthProvider's `me` effect loads the user.
 */
export function AuthCallback() {
  const { applyToken } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    const hash = window.location.hash.replace(/^#/, "");
    const token = new URLSearchParams(hash).get("token");
    // Strip the fragment so the token isn't left in the address bar / history.
    window.history.replaceState(null, "", window.location.pathname);

    if (!token) {
      setError("No sign-in token was returned. Please try again.");
      return;
    }
    applyToken(token);
    navigate("/", { replace: true });
  }, [applyToken, navigate]);

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      {error ? (
        <div className="text-center">
          <p className="text-sm text-destructive">{error}</p>
          <a href="/login" className="text-sm underline">
            Back to sign in
          </a>
        </div>
      ) : (
        <p className="text-muted-foreground">Completing sign-in…</p>
      )}
    </div>
  );
}
