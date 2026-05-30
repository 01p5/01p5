"""
Auth for the Olympus dashboard.

Phase A: Google OAuth sign-in + HMAC-signed session cookie + domain
allowlist + AUTH_BYPASS env for local dev. Phase B will add an email-OTP
fallback that lands on the same Session machinery.

The dashboard's HTTP layer is a plain ``BaseHTTPRequestHandler`` (no
framework). Keep this module dependency-light: stdlib only, plus
``google-auth`` for verifying Google's signed id_tokens (the one place
hand-rolled JWT verification would be a bad idea).

Public/protected split is enforced in ``server.py`` via
``Authenticator.is_public_get`` / ``is_public_post`` — anything that isn't
``/healthz``, ``/me``, ``/auth/*``, or a SPA static asset requires a valid
session (or ``AUTH_BYPASS=1``).
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

SESSION_COOKIE = "olympus_session"
OAUTH_STATE_COOKIE = "olympus_oauth_state"
DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days
DEFAULT_OTP_TTL_SECONDS = 600                # 10 min — code expires
DEFAULT_OTP_RATE_LIMIT_SECONDS = 60          # 1 issue per email per minute


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class AuthConfig:
    """Auth settings — usually read from environment by ``from_env``.

    ``bypass``: when True, every request looks authed as ``dev_email`` —
    use for local dev only. ``cookie_secure`` should stay True in prod
    (the cookie is then only sent over HTTPS).
    """
    bypass: bool = False
    dev_email: str = "dev@local"
    allowed_domains: frozenset[str] = frozenset()
    session_secret: bytes = b""
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    cookie_secure: bool = True
    google_client_id: str = ""
    google_client_secret: str = ""
    redirect_base_url: str = ""  # e.g. https://0lympu5.com — used to build the OAuth redirect_uri
    # Email-OTP fallback (Phase B). SMTP creds + a "from" address. When
    # smtp_host is empty, the email-code routes return 503.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_starttls: bool = True
    otp_ttl_seconds: int = DEFAULT_OTP_TTL_SECONDS
    otp_rate_limit_seconds: int = DEFAULT_OTP_RATE_LIMIT_SECONDS

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "AuthConfig":
        e = env if env is not None else os.environ
        bypass = _env_truthy(e.get("OLYMPUS_AUTH_BYPASS"))
        domains = frozenset(
            d.strip().lower() for d in (e.get("OLYMPUS_AUTH_ALLOWED_DOMAINS") or "").split(",")
            if d.strip()
        )
        secret_raw = e.get("OLYMPUS_AUTH_SESSION_SECRET") or ""
        if secret_raw:
            session_secret = secret_raw.encode()
        else:
            # Ephemeral key for dev: random bytes per-process. Sessions don't
            # survive restart, which is fine since you're in dev. Warn loudly
            # so we don't silently use this in prod.
            session_secret = secrets.token_bytes(32)
            if not bypass:
                logger.warning(
                    "OLYMPUS_AUTH_SESSION_SECRET not set — using an ephemeral "
                    "in-process key; sessions will be invalidated on restart."
                )
        ttl = int(e.get("OLYMPUS_AUTH_SESSION_TTL_SECONDS") or DEFAULT_SESSION_TTL_SECONDS)
        cookie_secure = _env_truthy(e.get("OLYMPUS_AUTH_COOKIE_SECURE", "1"))
        return cls(
            bypass=bypass,
            dev_email=e.get("OLYMPUS_AUTH_DEV_EMAIL", "dev@local"),
            allowed_domains=domains,
            session_secret=session_secret,
            session_ttl_seconds=ttl,
            cookie_secure=cookie_secure,
            google_client_id=e.get("OLYMPUS_AUTH_GOOGLE_CLIENT_ID", ""),
            google_client_secret=e.get("OLYMPUS_AUTH_GOOGLE_CLIENT_SECRET", ""),
            redirect_base_url=(e.get("OLYMPUS_AUTH_REDIRECT_BASE_URL") or "").rstrip("/"),
            smtp_host=e.get("OLYMPUS_AUTH_SMTP_HOST", ""),
            smtp_port=int(e.get("OLYMPUS_AUTH_SMTP_PORT") or 587),
            smtp_username=e.get("OLYMPUS_AUTH_SMTP_USERNAME", ""),
            smtp_password=e.get("OLYMPUS_AUTH_SMTP_PASSWORD", ""),
            smtp_from=e.get("OLYMPUS_AUTH_SMTP_FROM", ""),
            smtp_use_starttls=_env_truthy(e.get("OLYMPUS_AUTH_SMTP_STARTTLS", "1")),
            otp_ttl_seconds=int(e.get("OLYMPUS_AUTH_OTP_TTL_SECONDS") or DEFAULT_OTP_TTL_SECONDS),
            otp_rate_limit_seconds=int(
                e.get("OLYMPUS_AUTH_OTP_RATE_LIMIT_SECONDS") or DEFAULT_OTP_RATE_LIMIT_SECONDS
            ),
        )

    def is_email_allowed(self, email: str) -> bool:
        if not email or "@" not in email:
            return False
        # Allowlist empty = nobody (fail closed) unless bypass is on.
        if not self.allowed_domains:
            return False
        return email.rsplit("@", 1)[1].lower() in self.allowed_domains

    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.redirect_base_url)

    def google_redirect_uri(self) -> str:
        return f"{self.redirect_base_url}/auth/google/callback"

    def email_otp_enabled(self) -> bool:
        """True iff SMTP creds are wired (host + from at minimum)."""
        return bool(self.smtp_host and self.smtp_from)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Session:
    email: str
    expires_at: int = 0  # unix seconds; 0 = bypass / not applicable


def _b64url_encode(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _b64url_decode(s: bytes) -> bytes:
    pad = b"=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_session(email: str, secret: bytes, ttl_seconds: int, *, now: Optional[int] = None) -> str:
    """Return a ``body.sig`` token. Stateless; verified by the same secret."""
    now = int(now if now is not None else time.time())
    payload = json.dumps({"email": email, "iat": now, "exp": now + ttl_seconds}, separators=(",", ":")).encode()
    body = _b64url_encode(payload)
    sig = hmac.new(secret, body, hashlib.sha256).digest()
    return (body + b"." + _b64url_encode(sig)).decode("ascii")


def verify_session(token: str, secret: bytes, *, now: Optional[int] = None) -> Optional[Session]:
    """Return a ``Session`` if the token is signed, unexpired, and parses;
    ``None`` for any failure (tamper, expired, malformed)."""
    if not token or "." not in token:
        return None
    try:
        body_b, sig_b = token.encode("ascii").rsplit(b".", 1)
    except (ValueError, UnicodeEncodeError):
        return None
    expected = _b64url_encode(hmac.new(secret, body_b, hashlib.sha256).digest())
    if not hmac.compare_digest(sig_b, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(body_b))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = int(payload.get("exp") or 0)
    if int(now if now is not None else time.time()) > exp:
        return None
    email = payload.get("email")
    if not isinstance(email, str) or not email:
        return None
    return Session(email=email, expires_at=exp)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def parse_cookie_header(header: str, name: str) -> Optional[str]:
    """Extract one cookie value from a ``Cookie:`` header string. Returns
    None if absent. Handles quoting and multiple cookies."""
    if not header:
        return None
    for chunk in header.split(";"):
        k, _, v = chunk.strip().partition("=")
        if k == name:
            return v.strip().strip('"')
    return None


def set_cookie_header(name: str, value: str, *, max_age: int, secure: bool, path: str = "/") -> str:
    parts = [f"{name}={value}", f"Max-Age={max_age}", f"Path={path}", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(name: str, *, secure: bool, path: str = "/") -> str:
    parts = [f"{name}=", "Max-Age=0", f"Path={path}", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Authenticator + public-path policy
# ---------------------------------------------------------------------------

# Only these API/SSE/data prefixes require a session. Everything else
# (the SPA shell at /, /login, /chat, /sessions, /assets/*, /healthz,
# /me, /auth/*) is public so the unauthenticated SPA can load and render
# its own /login page. The SPA gates user-facing UI via RequireAuth.
_GATED_GET_PREFIXES = (
    "/tasks", "/events", "/approvals", "/audit", "/tools",
    "/memory", "/rollback", "/telemetry", "/mcp", "/stacks", "/tickets",
    "/inventory",
)
_GATED_POST_PREFIXES = (
    "/tasks", "/approvals/", "/memory/", "/rollback/",
    "/mcp/servers", "/tools/", "/tickets/", "/inventory/",
)
_GATED_PUT_PREFIXES = (
    "/inventory/",
)
_GATED_DELETE_PREFIXES = (
    "/inventory/", "/mcp/servers/",
)


def _path_matches(path: str, prefixes: tuple[str, ...]) -> bool:
    """Match either an exact prefix (``/tasks``) or a sub-path
    (``/tasks/T1``). Sub-path requires the next char to be ``/`` so
    ``/tasksomething`` doesn't accidentally match ``/tasks``."""
    for p in prefixes:
        if p.endswith("/"):
            if path.startswith(p):
                return True
        else:
            if path == p or path.startswith(p + "/"):
                return True
    return False


# ---------------------------------------------------------------------------
# Email-OTP (Phase B)
# ---------------------------------------------------------------------------

class RateLimitedError(RuntimeError):
    """Raised when an OTP issue request is too soon after the previous one."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"rate limited; retry in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _OTPRecord:
    code: str
    expires_at: int
    issued_at: int


class OTPStore:
    """In-memory single-use email-OTP store.

    Codes are short numeric strings (default 6 digits), unique per email,
    expire after ``ttl_seconds``, and are consumed on a successful verify.
    Per-email rate limit prevents spamming the SMTP server."""

    def __init__(self, ttl_seconds: int = DEFAULT_OTP_TTL_SECONDS, rate_limit_seconds: int = DEFAULT_OTP_RATE_LIMIT_SECONDS, code_length: int = 6):
        self.ttl_seconds = ttl_seconds
        self.rate_limit_seconds = rate_limit_seconds
        self.code_length = code_length
        self._records: dict[str, _OTPRecord] = {}
        self._lock = threading.RLock()

    def _now(self) -> int:
        return int(time.time())

    def _gen_code(self) -> str:
        # Cryptographic randomness; left-pad to fixed length.
        n = secrets.randbelow(10 ** self.code_length)
        return str(n).zfill(self.code_length)

    def issue(self, email: str) -> str:
        """Generate (and store) a new code for ``email``. Raises
        ``RateLimitedError`` if a previous code was issued recently."""
        key = email.strip().lower()
        now = self._now()
        with self._lock:
            prev = self._records.get(key)
            if prev is not None and prev.issued_at + self.rate_limit_seconds > now:
                raise RateLimitedError(prev.issued_at + self.rate_limit_seconds - now)
            code = self._gen_code()
            self._records[key] = _OTPRecord(
                code=code, expires_at=now + self.ttl_seconds, issued_at=now,
            )
            return code

    def verify(self, email: str, code: str) -> bool:
        """Constant-time compare + single-use consumption on success."""
        key = email.strip().lower()
        now = self._now()
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                return False
            if now > rec.expires_at:
                self._records.pop(key, None)
                return False
            if not hmac.compare_digest(rec.code.encode(), str(code).strip().encode()):
                return False
            # Consume on success.
            self._records.pop(key, None)
            return True


class EmailSender(Protocol):
    def send_otp(self, email: str, code: str) -> None: ...


@dataclass
class SmtpSender:
    """SMTP-backed EmailSender. Uses STARTTLS by default; auth only if a
    username is provided. Sends a small plain-text message."""
    host: str
    port: int
    from_addr: str
    username: str = ""
    password: str = ""
    use_starttls: bool = True
    timeout_seconds: int = 10

    def send_otp(self, email: str, code: str) -> None:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = email
        msg["Subject"] = f"Olympus sign-in code: {code}"
        msg.set_content(
            f"Your Olympus sign-in code is:\n\n  {code}\n\n"
            f"It expires in a few minutes. If you didn't request this, ignore it."
        )
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as s:
            if self.use_starttls:
                s.starttls()
            if self.username:
                s.login(self.username, self.password)
            s.send_message(msg)


def smtp_sender_from_config(config: AuthConfig) -> Optional[SmtpSender]:
    if not config.email_otp_enabled():
        return None
    return SmtpSender(
        host=config.smtp_host,
        port=config.smtp_port,
        from_addr=config.smtp_from,
        username=config.smtp_username,
        password=config.smtp_password,
        use_starttls=config.smtp_use_starttls,
    )


class Authenticator:
    """Validates session cookies (or bypasses entirely when configured)
    and decides which routes need auth."""

    def __init__(self, config: AuthConfig, *, email_sender: Optional[EmailSender] = None, otp_store: Optional[OTPStore] = None):
        self.config = config
        # Email-OTP backend — when None and SMTP creds are configured,
        # default to SmtpSender; explicit None disables email login.
        self.email_sender: Optional[EmailSender] = email_sender if email_sender is not None else smtp_sender_from_config(config)
        self.otp_store = otp_store or OTPStore(
            ttl_seconds=config.otp_ttl_seconds,
            rate_limit_seconds=config.otp_rate_limit_seconds,
        )

    # ---- gating policy ----

    @staticmethod
    def requires_auth_get(path: str) -> bool:
        return _path_matches(path.partition("?")[0], _GATED_GET_PREFIXES)

    @staticmethod
    def requires_auth_post(path: str) -> bool:
        return _path_matches(path.partition("?")[0], _GATED_POST_PREFIXES)

    @staticmethod
    def requires_auth_put(path: str) -> bool:
        return _path_matches(path.partition("?")[0], _GATED_PUT_PREFIXES)

    @staticmethod
    def requires_auth_delete(path: str) -> bool:
        return _path_matches(path.partition("?")[0], _GATED_DELETE_PREFIXES)

    # ---- session resolution ----

    def session_from_request(self, headers) -> Optional[Session]:
        """Return the authenticated session for this request, or None.

        ``headers`` is a mapping with ``.get`` (works for both
        ``http.client.HTTPMessage`` and a plain dict)."""
        if self.config.bypass:
            return Session(email=self.config.dev_email)
        cookie_hdr = ""
        if headers is not None:
            cookie_hdr = headers.get("Cookie", "") or headers.get("cookie", "") or ""
        token = parse_cookie_header(cookie_hdr, SESSION_COOKIE)
        if not token:
            return None
        return verify_session(token, self.config.session_secret)

    # ---- session minting ----

    def mint_cookie(self, email: str) -> str:
        token = sign_session(email, self.config.session_secret, self.config.session_ttl_seconds)
        return set_cookie_header(
            SESSION_COOKIE, token,
            max_age=self.config.session_ttl_seconds,
            secure=self.config.cookie_secure,
        )

    def clear_cookie(self) -> str:
        return clear_cookie_header(SESSION_COOKIE, secure=self.config.cookie_secure)


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleAuthError(RuntimeError):
    """OAuth flow failure — surfaces as a 400/401 from the callback."""


def google_authorize_url(config: AuthConfig, state: str) -> str:
    """Build the URL to send the browser to for Google OAuth consent."""
    if not config.google_oauth_enabled():
        raise GoogleAuthError("Google OAuth not configured")
    params = {
        "client_id": config.google_client_id,
        "redirect_uri": config.google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def new_oauth_state() -> str:
    """Random URL-safe state token (CSRF protection on the callback)."""
    return secrets.token_urlsafe(24)


def exchange_code_for_email(
    config: AuthConfig,
    code: str,
    *,
    token_fetcher=None,
    id_token_verifier=None,
) -> str:
    """Exchange an OAuth ``code`` for an id_token, verify it, return the
    authenticated email. Raises ``GoogleAuthError`` on any failure.

    ``token_fetcher`` and ``id_token_verifier`` are injectable so tests
    can drive the flow without hitting Google. Defaults call the real
    Google endpoints via stdlib urllib + ``google-auth``."""
    if not config.google_oauth_enabled():
        raise GoogleAuthError("Google OAuth not configured")
    fetcher = token_fetcher or _default_token_fetcher
    verifier = id_token_verifier or _default_id_token_verifier

    try:
        token_response = fetcher(
            code=code,
            client_id=config.google_client_id,
            client_secret=config.google_client_secret,
            redirect_uri=config.google_redirect_uri(),
        )
    except Exception as exc:
        raise GoogleAuthError(f"token exchange failed: {exc}") from exc

    id_token = token_response.get("id_token")
    if not id_token:
        raise GoogleAuthError("Google response missing id_token")

    try:
        claims = verifier(id_token, audience=config.google_client_id)
    except Exception as exc:
        raise GoogleAuthError(f"id_token verification failed: {exc}") from exc

    email = claims.get("email")
    if not email or not claims.get("email_verified"):
        raise GoogleAuthError("Google account missing verified email")
    return email


def _default_token_fetcher(*, code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """POST the code to Google's token endpoint via stdlib urllib."""
    import urllib.request

    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        _GOOGLE_TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _default_id_token_verifier(id_token_str: str, *, audience: str) -> dict:
    """Verify the id_token's signature + audience against Google's JWKs."""
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token as g_id_token

    return g_id_token.verify_oauth2_token(
        id_token_str, g_requests.Request(), audience=audience
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _env_truthy(v: Optional[str]) -> bool:
    if v is None:
        return False
    return v.strip().lower() in ("1", "true", "yes", "on")


# Re-export a snapshot of config fields useful for diagnostics (never the secret).
def public_status(config: AuthConfig) -> dict:
    return {
        "bypass": config.bypass,
        "google_oauth": config.google_oauth_enabled(),
        "email_otp": config.email_otp_enabled(),
        "allowed_domains": sorted(config.allowed_domains),
        "cookie_secure": config.cookie_secure,
    }


# Keep dataclasses importable as JSON for diagnostics if ever needed.
def _config_to_dict(config: AuthConfig) -> dict:
    d = dataclasses.asdict(config)
    d["session_secret"] = "<redacted>"
    d["google_client_secret"] = "<redacted>"
    d["allowed_domains"] = sorted(config.allowed_domains)
    return d
