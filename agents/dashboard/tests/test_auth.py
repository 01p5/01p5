"""
Auth (Phase A): session sign/verify, AuthConfig, Authenticator gating
policy, Google OAuth flow (with injected fetcher/verifier), and the
dashboard's /me + /auth/* routes + the gate on protected APIs.
"""
from __future__ import annotations

import http.client
import json
from typing import Any, Sequence

import pytest
from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    ManualRouter,
    Orchestrator,
    QueueApprovalHook,
    TaskMessage,
)
from dashboard import server as server_mod
from dashboard.auth import (
    AuthConfig,
    Authenticator,
    GoogleAuthError,
    exchange_code_for_email,
    google_authorize_url,
    parse_cookie_header,
    sign_session,
    verify_session,
)
from dashboard.server import DashboardServer


# ---------------------------------------------------------------------------
# Session sign/verify
# ---------------------------------------------------------------------------

SECRET = b"x" * 32


def test_sign_then_verify_round_trips():
    token = sign_session("user@stanford.edu", SECRET, ttl_seconds=60)
    s = verify_session(token, SECRET)
    assert s is not None
    assert s.email == "user@stanford.edu"


def test_verify_rejects_tampered_body():
    token = sign_session("user@stanford.edu", SECRET, ttl_seconds=60)
    body, sig = token.split(".")
    tampered = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    assert verify_session(tampered, SECRET) is None


def test_verify_rejects_wrong_secret():
    token = sign_session("u@stanford.edu", SECRET, ttl_seconds=60)
    assert verify_session(token, b"y" * 32) is None


def test_verify_rejects_expired():
    token = sign_session("u@stanford.edu", SECRET, ttl_seconds=60, now=1000)
    assert verify_session(token, SECRET, now=5000) is None


def test_verify_rejects_malformed():
    for bad in ("", "no-dot", "a.b.c.d", "abc.", ".abc", "not-base64.def"):
        assert verify_session(bad, SECRET) is None


# ---------------------------------------------------------------------------
# AuthConfig + Authenticator
# ---------------------------------------------------------------------------

def _cfg(**kw) -> AuthConfig:
    base = dict(
        bypass=False,
        allowed_domains=frozenset({"stanford.edu", "tianleyu.com"}),
        session_secret=SECRET,
        cookie_secure=False,
    )
    base.update(kw)
    return AuthConfig(**base)


def test_is_email_allowed_filters_by_domain():
    c = _cfg()
    assert c.is_email_allowed("alice@stanford.edu") is True
    assert c.is_email_allowed("bob@Tianleyu.com") is True  # case-insensitive
    assert c.is_email_allowed("eve@gmail.com") is False
    assert c.is_email_allowed("") is False
    assert c.is_email_allowed("no-at-sign") is False


def test_empty_allowlist_rejects_everyone():
    c = _cfg(allowed_domains=frozenset())
    assert c.is_email_allowed("alice@stanford.edu") is False


def test_from_env_parses_domains_and_truthy_bypass():
    env = {
        "OLYMPUS_AUTH_BYPASS": "true",
        "OLYMPUS_AUTH_ALLOWED_DOMAINS": "stanford.edu, tianleyu.com ,",
        "OLYMPUS_AUTH_SESSION_SECRET": "abcdef",
        "OLYMPUS_AUTH_COOKIE_SECURE": "0",
    }
    cfg = AuthConfig.from_env(env)
    assert cfg.bypass is True
    assert cfg.allowed_domains == frozenset({"stanford.edu", "tianleyu.com"})
    assert cfg.cookie_secure is False
    assert cfg.session_secret == b"abcdef"


def test_from_env_ephemeral_secret_when_unset():
    cfg = AuthConfig.from_env({"OLYMPUS_AUTH_BYPASS": "1"})
    assert len(cfg.session_secret) >= 16


def test_authenticator_bypass_authes_dev_email():
    a = Authenticator(_cfg(bypass=True, dev_email="dev@local"))
    s = a.session_from_request({"Cookie": ""})
    assert s is not None and s.email == "dev@local"


def test_authenticator_no_cookie_no_session():
    a = Authenticator(_cfg())
    assert a.session_from_request({"Cookie": ""}) is None
    assert a.session_from_request(None) is None


def test_authenticator_valid_cookie_returns_session():
    cfg = _cfg()
    a = Authenticator(cfg)
    token = sign_session("u@stanford.edu", cfg.session_secret, ttl_seconds=60)
    s = a.session_from_request({"Cookie": f"olympus_session={token}; other=x"})
    assert s and s.email == "u@stanford.edu"


def test_gating_policy_marks_correct_paths():
    # Gated (API/SSE/data) — needs auth.
    for p in ("/tasks", "/tasks/T1", "/tasks/T1/events", "/events", "/tickets",
              "/tickets/T1", "/tickets/T1/events", "/approvals", "/audit",
              "/tools", "/memory", "/rollback", "/telemetry", "/mcp/servers",
              "/stacks/terraform"):
        assert Authenticator.requires_auth_get(p) is True, p
    # Public — no auth needed.
    for p in ("/", "/index.html", "/healthz", "/me", "/auth/google/start",
              "/auth/google/callback", "/login", "/chat", "/sessions",
              "/assets/index-abc.js", "/static/index.html", "/favicon.svg"):
        assert Authenticator.requires_auth_get(p) is False, p
    # Don't match prefix collisions like /tasksomething.
    assert Authenticator.requires_auth_get("/tasksomething") is False
    # POST gating: only API mutations require auth.
    for p in ("/tasks", "/approvals/abc", "/memory/T1/feedback",
              "/rollback/T1/execute", "/mcp/servers", "/tools/sysadmin/get_pods",
              "/tickets/T1/messages", "/tickets/T1/close"):
        assert Authenticator.requires_auth_post(p) is True, p
    assert Authenticator.requires_auth_post("/auth/logout") is False


# ---------------------------------------------------------------------------
# Google OAuth helpers
# ---------------------------------------------------------------------------

def _google_cfg() -> AuthConfig:
    return _cfg(
        google_client_id="cid",
        google_client_secret="csec",
        redirect_base_url="https://0lympu5.com",
    )


def test_google_authorize_url_includes_required_params():
    url = google_authorize_url(_google_cfg(), state="xyz")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid" in url
    assert "state=xyz" in url
    assert "scope=openid+email+profile" in url
    assert "redirect_uri=https%3A%2F%2F0lympu5.com%2Fauth%2Fgoogle%2Fcallback" in url


def test_google_authorize_url_raises_when_unconfigured():
    with pytest.raises(GoogleAuthError):
        google_authorize_url(_cfg(), state="x")


def test_exchange_code_for_email_happy_path():
    cfg = _google_cfg()
    captured: dict = {}

    def fake_fetcher(*, code, client_id, client_secret, redirect_uri):
        captured.update(dict(code=code, client_id=client_id, redirect_uri=redirect_uri))
        return {"id_token": "fake_id_token"}

    def fake_verifier(id_tok, *, audience):
        captured["audience"] = audience
        return {"email": "alice@stanford.edu", "email_verified": True}

    email = exchange_code_for_email(cfg, "the_code",
                                    token_fetcher=fake_fetcher,
                                    id_token_verifier=fake_verifier)
    assert email == "alice@stanford.edu"
    assert captured["code"] == "the_code"
    assert captured["client_id"] == "cid"
    assert captured["audience"] == "cid"


def test_exchange_code_rejects_unverified_email():
    cfg = _google_cfg()
    with pytest.raises(GoogleAuthError, match="verified email"):
        exchange_code_for_email(
            cfg, "code",
            token_fetcher=lambda **_: {"id_token": "x"},
            id_token_verifier=lambda t, *, audience: {"email": "a@stanford.edu", "email_verified": False},
        )


def test_exchange_code_no_id_token():
    cfg = _google_cfg()
    with pytest.raises(GoogleAuthError, match="id_token"):
        exchange_code_for_email(
            cfg, "code",
            token_fetcher=lambda **_: {},
            id_token_verifier=lambda *a, **k: {},
        )


def test_exchange_code_token_fetch_error():
    cfg = _google_cfg()

    def boom(**_):
        raise RuntimeError("network down")

    with pytest.raises(GoogleAuthError, match="token exchange"):
        exchange_code_for_email(cfg, "code", token_fetcher=boom)


def test_exchange_code_verifier_error():
    cfg = _google_cfg()
    with pytest.raises(GoogleAuthError, match="id_token verification"):
        exchange_code_for_email(
            cfg, "code",
            token_fetcher=lambda **_: {"id_token": "x"},
            id_token_verifier=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad sig")),
        )


def test_exchange_code_raises_when_unconfigured():
    with pytest.raises(GoogleAuthError):
        exchange_code_for_email(_cfg(), "code")


def test_parse_cookie_header_handles_quotes_and_missing():
    assert parse_cookie_header("a=1; b=\"two\"; c=3", "b") == "two"
    assert parse_cookie_header("a=1; b=2", "missing") is None
    assert parse_cookie_header("", "any") is None


# ---------------------------------------------------------------------------
# DashboardServer auth gate + /me + /auth routes (live HTTP)
# ---------------------------------------------------------------------------

class _EchoAgent(AgentSpec):
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()
    name = "stub"
    domain = "stub"

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, status="success",
            summary="ok", cost=CostBreakdown(),
        )


def _build_server(auth: Authenticator | None) -> DashboardServer:
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=[_EchoAgent()], ctx=ctx,
        router=ManualRouter(default="stub"), result_timeout_seconds=5.0,
    )
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, auth=auth,
    )
    srv.serve()
    return srv


def _get(srv, path, *, cookie=None, timeout=5.0):
    """http.client GET — doesn't auto-follow redirects, so 302s surface.
    Returns (status, msg, body); msg is an ``email.message.Message`` so
    ``msg.get(name)`` returns the first value, ``msg.get_all(name)`` returns
    all values (important for multi-valued ``Set-Cookie``)."""
    host, port = srv.address
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        headers = {"Cookie": cookie} if cookie else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.msg, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def _post(srv, path, body=None, *, cookie=None, timeout=5.0):
    host, port = srv.address
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request("POST", path, json.dumps(body or {}).encode(), headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.msg, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def _set_cookies(msg) -> list[str]:
    """All Set-Cookie header values from an HTTP response message."""
    return msg.get_all("Set-Cookie") or []


def _cookie_value(msg, name: str) -> str | None:
    """Find one Set-Cookie value across multiple Set-Cookie headers."""
    for sc in _set_cookies(msg):
        v = parse_cookie_header(sc, name)
        if v is not None:
            return v
    return None


@pytest.fixture
def auth_server():
    cfg = _cfg(google_client_id="cid", google_client_secret="csec",
               redirect_base_url="http://x")
    srv = _build_server(Authenticator(cfg))
    yield srv, cfg
    srv.shutdown()


def test_protected_api_returns_401_without_session(auth_server):
    srv, _ = auth_server
    code, _, body = _get(srv, "/tasks")
    assert code == 401
    assert "unauthenticated" in body


def test_healthz_is_public(auth_server):
    srv, _ = auth_server
    code, _, _b = _get(srv, "/healthz")
    assert code == 200


def test_spa_shell_is_public_for_unmatched_get(tmp_path, monkeypatch):
    # The SPA fallback (any unmatched GET → index.html) must serve without auth.
    cfg = _cfg()
    (tmp_path / "index.html").write_text("<html>spa</html>")
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(bus=bus, agents=[_EchoAgent()], ctx=ctx,
                        router=ManualRouter(default="stub"))
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, static_dir=tmp_path,
        auth=Authenticator(cfg),
    )
    srv.serve()
    try:
        code, _, body = _get(srv, "/login")  # SPA client route, no API
        assert code == 200
        assert "spa" in body
    finally:
        srv.shutdown()


def test_me_returns_401_when_unauthed(auth_server):
    srv, _ = auth_server
    code, _, body = _get(srv, "/me")
    assert code == 401
    assert json.loads(body)["authenticated"] is False


def test_me_returns_email_with_valid_session(auth_server):
    srv, cfg = auth_server
    token = sign_session("alice@stanford.edu", cfg.session_secret, ttl_seconds=60)
    code, _, body = _get(srv, "/me", cookie=f"olympus_session={token}")
    assert code == 200
    payload = json.loads(body)
    assert payload["authenticated"] is True
    assert payload["email"] == "alice@stanford.edu"
    assert "auth" in payload  # diagnostic block


def test_valid_session_unlocks_protected_api(auth_server):
    srv, cfg = auth_server
    token = sign_session("u@stanford.edu", cfg.session_secret, ttl_seconds=60)
    code, _, _b = _get(srv, "/tasks", cookie=f"olympus_session={token}")
    assert code == 200


def test_logout_clears_cookie(auth_server):
    srv, _ = auth_server
    code, headers, body = _post(srv, "/auth/logout")
    assert code == 200
    sc_all = " ".join(_set_cookies(headers))
    assert "olympus_session=" in sc_all and "Max-Age=0" in sc_all
    assert json.loads(body) == {"ok": True}


def test_google_start_redirects_with_state_cookie(auth_server):
    srv, _ = auth_server
    code, headers, _b = _get(srv, "/auth/google/start")
    assert code == 302
    loc = headers.get("Location", "")
    assert loc.startswith("https://accounts.google.com/")
    # The state in the URL must match the state cookie.
    from urllib.parse import parse_qs, urlparse
    state_in_url = parse_qs(urlparse(loc).query)["state"][0]
    state_in_cookie = _cookie_value(headers, "olympus_oauth_state")
    assert state_in_url == state_in_cookie


def test_google_start_503_when_not_configured():
    srv = _build_server(Authenticator(_cfg()))  # no google client config
    try:
        code, _, body = _get(srv, "/auth/google/start")
        assert code == 503
        assert "not configured" in body
    finally:
        srv.shutdown()


def test_google_callback_happy_path_sets_session(auth_server, monkeypatch):
    srv, cfg = auth_server
    # Patch the OAuth exchange to skip Google entirely.
    monkeypatch.setattr(
        server_mod, "exchange_code_for_email",
        lambda c, code, **kw: "bob@tianleyu.com",
    )
    code, headers, _b = _get(
        srv, "/auth/google/callback?code=THECODE&state=STATE",
        cookie="olympus_oauth_state=STATE",
    )
    assert code == 302
    assert headers.get("Location") == "/"
    # Two Set-Cookie headers expected: session + cleared oauth_state.
    token = _cookie_value(headers, "olympus_session")
    assert token, _set_cookies(headers)
    s = verify_session(token, cfg.session_secret)
    assert s and s.email == "bob@tianleyu.com"


def test_google_callback_rejects_email_outside_allowlist(auth_server, monkeypatch):
    srv, _ = auth_server
    monkeypatch.setattr(
        server_mod, "exchange_code_for_email",
        lambda c, code, **kw: "eve@gmail.com",
    )
    code, _, body = _get(
        srv, "/auth/google/callback?code=C&state=S",
        cookie="olympus_oauth_state=S",
    )
    assert code == 403
    assert "email_not_allowed" in body


def test_google_callback_rejects_bad_state(auth_server):
    srv, _ = auth_server
    code, _, body = _get(srv, "/auth/google/callback?code=C&state=MISMATCH",
                         cookie="olympus_oauth_state=EXPECTED")
    assert code == 400
    assert "invalid_state" in body


def test_google_callback_surfaces_oauth_error(auth_server):
    srv, _ = auth_server
    code, _, body = _get(srv, "/auth/google/callback?error=access_denied")
    assert code == 400
    assert "oauth_error" in body


def test_google_callback_surfaces_google_failure(auth_server, monkeypatch):
    srv, _ = auth_server

    def boom(c, code, **kw):
        raise GoogleAuthError("verification blew up")

    monkeypatch.setattr(server_mod, "exchange_code_for_email", boom)
    code, _, body = _get(srv, "/auth/google/callback?code=C&state=S",
                         cookie="olympus_oauth_state=S")
    assert code == 401
    assert "oauth_failed" in body


def test_bypass_auth_makes_apis_open(auth_server=None):
    # AUTH_BYPASS path: any cookie-less request looks authed.
    cfg = _cfg(bypass=True)
    srv = _build_server(Authenticator(cfg))
    try:
        assert _get(srv, "/tasks")[0] == 200
        code, _, body = _get(srv, "/me")
        assert code == 200 and json.loads(body)["email"] == "dev@local"
    finally:
        srv.shutdown()
