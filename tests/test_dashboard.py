import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import bot.dashboard.server as server
from bot.dashboard.server import DashboardHandler, safe_state_path, scan_bots


@pytest.fixture()
def output_dir(tmp_path):
    (tmp_path / "live_state.json").write_text(
        json.dumps({"portfolio": {"cash": 1.0}}), encoding="utf-8")
    (tmp_path / "pol_state.json").write_text(
        json.dumps({"bot": "polymarket-paper"}), encoding="utf-8")
    (tmp_path / "backtest_sma_cross_1_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not json state", encoding="utf-8")
    return tmp_path


def test_scan_finds_only_json_states(output_dir):
    bots = scan_bots(output_dir)
    names = {b["file"] for b in bots}
    assert names == {
        "live_state.json",
        "pol_state.json",
        "backtest_sma_cross_1_summary.json",
    }
    by_file = {b["file"]: b for b in bots}
    assert by_file["live_state.json"]["name"] == "Live — Hyperliquid paper"
    assert by_file["pol_state.json"]["name"] == "Polymarket paper"


def test_safe_state_path_blocks_traversal(output_dir):
    assert safe_state_path("live_state.json", output_dir) is not None
    assert safe_state_path("../core/net.py", output_dir) is None
    assert safe_state_path("..\\core\\net.py", output_dir) is None
    assert safe_state_path("sub/dir.json", output_dir) is None
    assert safe_state_path("missing.json", output_dir) is None
    assert safe_state_path("", output_dir) is None
    assert safe_state_path(None, output_dir) is None


@pytest.fixture()
def live_server(output_dir, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, body


def test_api_bots_endpoint(live_server):
    status, body = _get(live_server.server_address[1], "/api/bots")
    assert status == 200
    assert "live_state.json" in body


def test_api_bot_serves_state(live_server):
    status, body = _get(live_server.server_address[1],
                        "/api/bot?file=live_state.json")
    assert status == 200
    assert json.loads(body)["portfolio"]["cash"] == 1.0


def test_api_bot_rejects_traversal(live_server):
    status, _ = _get(live_server.server_address[1],
                     "/api/bot?file=../core/net.py")
    assert status == 404


def test_index_served(live_server):
    status, body = _get(live_server.server_address[1], "/")
    assert status == 200
    assert "Dashboard" in body


def test_unknown_route_404(live_server):
    status, _ = _get(live_server.server_address[1], "/nope")
    assert status == 404
