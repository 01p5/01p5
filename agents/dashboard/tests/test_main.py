"""Coverage for dashboard.main — argparse parsing + router selection.

We patch build_default_server so we don't actually start an HTTP
listener; KeyboardInterrupt is simulated to drive the try/finally.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from dashboard import main as main_mod


def _make_fake_server():
    server = MagicMock()
    server.address = ("127.0.0.1", 8765)
    server._server_thread = MagicMock()
    server._server_thread.join.side_effect = KeyboardInterrupt
    return server


def test_main_default_args_starts_and_shuts_down_on_ctrl_c(capsys):
    fake = _make_fake_server()
    with patch("dashboard.main.build_default_server", return_value=fake) as build:
        rc = main_mod.main([])
    assert rc == 0
    # build_default_server was called with the parsed defaults.
    kwargs = build.call_args.kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8765
    assert kwargs["router"] is None   # default 'llm' → router=None (orchestrator picks LLMRouter)
    out = capsys.readouterr().out
    assert "listening on http://127.0.0.1:8765" in out
    assert "shutting down" in out
    fake.shutdown.assert_called_once()


def test_main_with_manual_router_imports_registry():
    fake = _make_fake_server()
    with patch("dashboard.main.build_default_server", return_value=fake) as build, \
         patch("olympus_cli.registry.manual_router", return_value="ROUTER") as mr:
        main_mod.main(["--router", "manual", "--port", "9999", "--verbose"])
    mr.assert_called_once()
    assert build.call_args.kwargs["router"] == "ROUTER"
    assert build.call_args.kwargs["port"] == 9999


def test_main_propagates_custom_host_audit_log(tmp_path):
    fake = _make_fake_server()
    log = str(tmp_path / "audit.jsonl")
    with patch("dashboard.main.build_default_server", return_value=fake) as build:
        main_mod.main(["--host", "0.0.0.0", "--audit-log", log])
    kw = build.call_args.kwargs
    assert kw["host"] == "0.0.0.0"
    assert kw["audit_log_path"] == log
