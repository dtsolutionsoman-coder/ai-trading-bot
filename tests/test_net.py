import urllib.error

import pytest

from bot.core.net import UnsafeURL, safe_urlopen, validate_public_http_url


def test_rejects_non_http_scheme():
    with pytest.raises(UnsafeURL):
        validate_public_http_url("ftp://example.com/file")


def test_rejects_empty_and_garbage():
    with pytest.raises(UnsafeURL):
        validate_public_http_url("")
    with pytest.raises(UnsafeURL):
        validate_public_http_url("not a url")


def test_rejects_localhost_by_name():
    with pytest.raises(UnsafeURL):
        validate_public_http_url("http://localhost:8080/api")


def test_rejects_loopback_ip():
    with pytest.raises(UnsafeURL):
        validate_public_http_url("http://127.0.0.1:8080/x")


def test_rejects_private_ip():
    with pytest.raises(UnsafeURL):
        validate_public_http_url("https://192.168.1.5/v1/chat/completions")
    with pytest.raises(UnsafeURL):
        validate_public_http_url("https://10.0.0.7/api")


def test_rejects_link_local_metadata_ip():
    with pytest.raises(UnsafeURL):
        validate_public_http_url("http://169.254.169.254/latest/meta-data")


def test_accepts_public_ip_literal():
    # numeric host: no DNS needed, works offline
    assert validate_public_http_url("https://8.8.8.8/v1") == "https://8.8.8.8/v1"


def test_safe_urlopen_enforces_host_allowlist():
    with pytest.raises(UnsafeURL):
        safe_urlopen(
            "https://api.binance.com/api/v3/klines",
            allowed_hosts={"example.com"},
        )


class _FakeOpener:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _http_error(code):
    return urllib.error.HTTPError("https://8.8.8.8/v1", code, "err", {}, None)


def _patched_net(monkeypatch, outcomes):
    import bot.core.net as net_mod
    fake = _FakeOpener(outcomes)
    monkeypatch.setattr(net_mod.urllib.request, "build_opener", lambda *a, **k: fake)
    monkeypatch.setattr(net_mod.time, "sleep", lambda s: None)
    return fake


def test_safe_urlopen_retries_transient_502(monkeypatch):
    sentinel = object()
    fake = _patched_net(monkeypatch, [_http_error(502), _http_error(503), sentinel])
    # numeric host: no DNS needed, works offline
    assert safe_urlopen("https://8.8.8.8/v1", retries=2) is sentinel
    assert fake.calls == 3


def test_safe_urlopen_retries_network_errors(monkeypatch):
    sentinel = object()
    fake = _patched_net(
        monkeypatch, [urllib.error.URLError("connection reset"), sentinel]
    )
    assert safe_urlopen("https://8.8.8.8/v1", retries=2) is sentinel
    assert fake.calls == 2


def test_safe_urlopen_raises_non_retryable_immediately(monkeypatch):
    fake = _patched_net(monkeypatch, [_http_error(404)])
    with pytest.raises(urllib.error.HTTPError):
        safe_urlopen("https://8.8.8.8/v1", retries=2)
    assert fake.calls == 1


def test_safe_urlopen_gives_up_after_retries(monkeypatch):
    fake = _patched_net(monkeypatch, [_http_error(502), _http_error(502)])
    with pytest.raises(urllib.error.HTTPError):
        safe_urlopen("https://8.8.8.8/v1", retries=1)
    assert fake.calls == 2


def test_safe_urlopen_default_keeps_old_behavior(monkeypatch):
    fake = _patched_net(monkeypatch, [_http_error(502)])
    with pytest.raises(urllib.error.HTTPError):
        safe_urlopen("https://8.8.8.8/v1")
    assert fake.calls == 1
