"""Dashboard CLI: python -m bot.dashboard [--port 8787]"""

from __future__ import annotations

import argparse

from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.dashboard")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (keep 127.0.0.1 unless you know why)")
    args = parser.parse_args(argv)
    serve(port=args.port, host=args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
