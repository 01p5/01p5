"""``olympus-dashboard`` CLI entry point."""
from __future__ import annotations

import argparse
import logging
import os
import sys

from .server import DEFAULT_AUDIT_LOG, build_default_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="olympus-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--router",
        choices=("llm", "manual"),
        default="llm",
        help="Router to use (default: llm). 'manual' is deterministic + offline.",
    )
    parser.add_argument(
        "--audit-log",
        default=os.path.expanduser(DEFAULT_AUDIT_LOG),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    router = None
    if args.router == "manual":
        from olympus_cli.registry import manual_router

        router = manual_router()

    server = build_default_server(
        host=args.host,
        port=args.port,
        router=router,
        audit_log_path=args.audit_log,
    )
    server.serve()
    host, port = server.address
    print(f"olympus dashboard listening on http://{host}:{port}", flush=True)
    try:
        server._server_thread.join()  # type: ignore[union-attr]
    except KeyboardInterrupt:
        print("shutting down…")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
