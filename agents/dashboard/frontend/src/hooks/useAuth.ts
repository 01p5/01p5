import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { AuthStatus } from "../types";

export type AuthState =
  | { state: "loading" }
  | { state: "authed"; email: string; status: AuthStatus }
  | { state: "unauthed"; status?: AuthStatus };

/**
 * Probe ``GET /me`` and expose the result. ``refresh()`` re-checks (used
 * after a successful login). RequireAuth uses ``state.state`` to gate
 * routing; the topnav uses ``email`` for the user chip + logout.
 */
export function useAuth(): { auth: AuthState; refresh: () => Promise<void> } {
  const [auth, setAuth] = useState<AuthState>({ state: "loading" });

  const refresh = useCallback(async () => {
    setAuth({ state: "loading" });
    try {
      const me = await api.me();
      if (me.authenticated && me.email) {
        setAuth({ state: "authed", email: me.email, status: me.auth });
      } else {
        setAuth({ state: "unauthed", status: me.auth });
      }
    } catch {
      // Network down or unexpected status — treat as unauthed without status.
      setAuth({ state: "unauthed" });
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return { auth, refresh };
}
