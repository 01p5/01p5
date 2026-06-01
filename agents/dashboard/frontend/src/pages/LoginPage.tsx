import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Sparkles, Mail, AlertCircle } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../hooks/useAuth";

/**
 * Sign-in page. Two paths: Google (button → /auth/google/start, browser
 * navigates), and email code (POST /auth/email/start → enter code →
 * POST /auth/email/verify → cookie set). Only shows methods that the
 * backend reports as enabled (from /me's auth status block).
 */
export function LoginPage(): JSX.Element {
  const { auth, refresh } = useAuth();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const next = search.get("next") || "/";

  // Already signed in? Bounce to the intended page.
  useEffect(() => {
    if (auth.state === "authed") {
      navigate(next, { replace: true });
    }
  }, [auth, navigate, next]);

  const [stage, setStage] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = auth.state === "unauthed" ? auth.status : (auth.state === "authed" ? auth.status : undefined);
  const googleOn = status?.google_oauth ?? false;
  const emailOn = status?.email_otp ?? false;

  const onEmailStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      await api.emailStart(email.trim());
      setStage("code");
    } catch (err) {
      setError(humanize((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const onVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      await api.emailVerify(email.trim(), code.trim());
      await refresh();
      navigate(next, { replace: true });
    } catch (err) {
      setError(humanize((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full flex items-center justify-center bg-dark-primary p-6">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-accent-green/10 border border-accent-green/30">
            <Sparkles size={20} className="text-accent-green" />
          </div>
          <h1 className="font-display text-xl font-semibold text-text-primary">Sign in to Olympus</h1>
          <p className="text-[12px] text-text-secondary">
            {status?.allowed_domains?.length
              ? `Allowed domains: ${status.allowed_domains.join(", ")}`
              : "Allowlisted accounts only."}
          </p>
        </div>

        {error && (
          <div className="flex items-start gap-2 text-[12px] font-mono text-accent-red bg-accent-red/10 border border-accent-red/30 rounded px-3 py-2">
            <AlertCircle size={14} strokeWidth={2.25} className="mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {googleOn && (
          <a
            href={api.googleStartUrl()}
            className="flex items-center justify-center gap-2 w-full bg-dark-panel border border-border-subtle hover:border-accent-green/40 text-text-primary rounded-md px-4 py-2.5 text-sm transition-colors"
          >
            <GoogleG size={18} />
            Continue with Google
          </a>
        )}

        {googleOn && emailOn && (
          <div className="flex items-center gap-3 text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted">
            <div className="flex-1 h-px bg-border-subtle" />
            or
            <div className="flex-1 h-px bg-border-subtle" />
          </div>
        )}

        {emailOn && stage === "email" && (
          <form onSubmit={onEmailStart} className="space-y-2">
            <label className="block text-[11px] font-mono uppercase tracking-[1.5px] text-text-muted">
              Email
            </label>
            <input
              type="email" required autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@stanford.edu"
              className="w-full bg-dark-panel border border-border-subtle rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60"
            />
            <button
              type="submit" disabled={busy || !email.trim()}
              className="w-full flex items-center justify-center gap-2 bg-accent-blue text-dark-primary font-semibold rounded-md px-4 py-2.5 hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Mail size={16} strokeWidth={2.25} />
              {busy ? "Sending…" : "Send sign-in code"}
            </button>
          </form>
        )}

        {emailOn && stage === "code" && (
          <form onSubmit={onVerify} className="space-y-2">
            <div className="text-[12px] text-text-secondary">
              We sent a 6-digit code to <code className="text-text-primary">{email}</code>.
            </div>
            <input
              type="text" inputMode="numeric" required autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              placeholder="123456"
              maxLength={6}
              className="w-full bg-dark-panel border border-border-subtle rounded-md px-3 py-2 text-lg font-mono tracking-[0.5em] text-center text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60"
            />
            <button
              type="submit" disabled={busy || code.length !== 6}
              className="w-full bg-accent-green text-dark-primary font-semibold rounded-md px-4 py-2.5 hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {busy ? "Verifying…" : "Sign in"}
            </button>
            <button
              type="button" onClick={() => { setStage("email"); setCode(""); setError(null); }}
              className="w-full text-[11px] font-mono text-text-muted hover:text-text-secondary"
            >
              ← use a different email
            </button>
          </form>
        )}

        {!googleOn && !emailOn && (
          <div className="text-[12px] text-text-secondary text-center">
            No sign-in methods are configured on this deployment.{" "}
            <Link to="/" className="text-accent-green hover:underline">Back home</Link>
          </div>
        )}
      </div>
    </div>
  );
}

// Inline Google G — lucide doesn't ship brand logos, and the multicolor
// official mark is the most recognizable affordance for "Sign in with Google".
function GoogleG({ size = 18 }: { size?: number }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path fill="#4285F4" d="M44.5 20H24v8.5h11.8C34.7 33.9 30.1 37 24 37c-7.2 0-13-5.8-13-13s5.8-13 13-13c3.1 0 5.9 1.1 8.1 2.9l6.4-6.4C34.6 4.1 29.6 2 24 2 11.8 2 2 11.8 2 24s9.8 22 22 22c11 0 21-8 21-22 0-1.3-.2-2.7-.5-4z"/>
      <path fill="#34A853" d="M6.3 14.7l7 5.1C15.1 16.1 19.2 13 24 13c3.1 0 5.9 1.1 8.1 2.9l6.4-6.4C34.6 4.1 29.6 2 24 2 16.3 2 9.7 6.6 6.3 14.7z"/>
      <path fill="#FBBC05" d="M24 46c5.5 0 10.5-2.1 14.3-5.5l-6.6-5.4c-2 1.4-4.5 2.3-7.7 2.3-6.1 0-11.3-4.1-13.2-9.7l-7 5.4C7.4 41 14.9 46 24 46z"/>
      <path fill="#EA4335" d="M44.5 20H24v8.5h11.8c-.6 2.8-2.4 5.1-4.7 6.6l6.6 5.4C42.5 36.8 46 31.3 46 24c0-1.3-.2-2.7-.5-4z"/>
    </svg>
  );
}

function humanize(msg: string): string {
  // Backend wraps `{error: "..."}` into "<status> <statusText>: <error>".
  // Surface friendlier text for the common cases.
  if (/email_not_allowed/.test(msg)) return "That email's domain isn't on the allowlist.";
  if (/rate_limited/.test(msg)) return "Too many requests — wait a minute and try again.";
  if (/invalid_or_expired_code/.test(msg)) return "Wrong or expired code. Try again or request a new one.";
  if (/not configured/.test(msg)) return "This sign-in method isn't configured.";
  return msg;
}
