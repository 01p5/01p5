import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

/**
 * Gates child routes behind a valid session. ``loading`` shows a small
 * placeholder; ``unauthed`` redirects to ``/login`` (preserving the
 * intended URL so we can come back after sign-in).
 */
export function RequireAuth({ children }: { children: JSX.Element }): JSX.Element {
  const { auth } = useAuth();
  const location = useLocation();

  if (auth.state === "loading") {
    return (
      <div className="h-full flex items-center justify-center bg-dark-primary text-text-muted font-mono text-sm">
        checking session…
      </div>
    );
  }
  if (auth.state === "unauthed") {
    const to = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    return <Navigate to={to} replace />;
  }
  return children;
}
