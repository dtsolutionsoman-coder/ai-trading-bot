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
