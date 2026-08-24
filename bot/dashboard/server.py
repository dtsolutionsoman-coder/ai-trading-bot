"""Local-only web dashboard.

Serves a single-page UI + JSON API over the state files in output/. Binds
127.0.0.1 by default so nothing outside this machine can reach it. Strictly
read-only: the API only serves files that live directly in output/ and match
a safe-name pattern (no path traversal, no arbitrary file reads).
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"
STATIC_DIR = Path(__file__).resolve().parent / "static"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.json$")

_DISPLAY_PREFIXES = [
    ("live_llm", "Live — GLM AI paper"),
    ("live_carry", "Live — Funding Carry paper"),
    ("race_sma", "Race — SMA control (15m)"),
    ("race_", "Race book"),
    ("live_state", "Live — Hyperliquid paper"),
    ("live_test", "Live (test run)"),
    ("pol_state", "Polymarket paper"),
    ("sol_sniper_state", "Solana sniper paper"),
    ("sol_copy_state", "Solana copy paper"),
]


def display_name(file_name: str) -> str:
    for prefix, name in _DISPLAY_PREFIXES:
        if file_name.startswith(prefix):
            return name
    if file_name.startswith("backtest_") and file_name.endswith("_summary.json"):
        return "Backtest run"
    return file_name


def scan_bots(output_dir: Path | None = None) -> list[dict]:
    output_dir = output_dir or OUTPUT_DIR
    if not output_dir.is_dir():
        return []
    out = []
    for path in sorted(output_dir.glob("*.json")):
        if not _NAME_RE.match(path.name):
            continue
        stat = path.stat()
        out.append(
            {
                "file": path.name,
                "name": display_name(path.name),
                "mtime": int(stat.st_mtime),
                "size": stat.st_size,
            }
        )
    return out


def safe_state_path(name: str, output_dir: Path | None = None) -> Path | None:
    """Resolve a requested state file, or None if the name is unsafe."""
    output_dir = output_dir or OUTPUT_DIR
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return None
    candidate = output_dir / name
    try:
        resolved = candidate.resolve()
        resolved.relative_to(output_dir.resolve())
    except (ValueError, OSError):
        return None
    return resolved if resolved.is_file() else None


class DashboardHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        route = parsed.path

        if route in ("/", "/index.html"):
            index = STATIC_DIR / "index.html"
            if index.is_file():
                self._send(200, index.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"index.html missing", "text/plain")
            return

        if route == "/api/bots":
            self._send_json(200, scan_bots())
            return

        if route == "/api/bot":
            params = parse_qs(parsed.query)
            name = (params.get("file") or [""])[0]
            path = safe_state_path(name)
            if path is None:
                self._send_json(404, {"error": "unknown state file"})
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                self._send_json(500, {"error": f"unreadable state: {exc}"})
                return
            self._send_json(200, data)
            return

        self._send(404, b"not found", "text/plain")

    def log_message(self, format, *args):  # keep the console quiet
        pass


def serve(port: int = 8787, host: str = "127.0.0.1") -> None:
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"dashboard running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped")
    finally:
        httpd.server_close()
