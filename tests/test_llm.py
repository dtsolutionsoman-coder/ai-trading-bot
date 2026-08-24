import pytest

from bot.llm.client import LLMClient, LLMError, _PLACEHOLDER_KEYS
from bot.strategies.llm_analyst import parse_decision


def test_parses_plain_json():
    d = parse_decision('{"action": "buy", "conviction": 0.7, "reason": "momentum"}')
    assert d == {"action": "buy", "conviction": 0.7, "reason": "momentum"}


def test_parses_fenced_json():
    raw = '```json\n{"action": "sell", "conviction": 0.4, "reason": "overbought"}\n```'
    d = parse_decision(raw)
    assert d["action"] == "sell" and d["conviction"] == 0.4


def test_parses_json_with_surrounding_prose():
    raw = (
        'Sure! Here is my decision: {"action": "hold", "conviction": 0.1, '
        '"reason": "mixed"} hope that helps'
    )
    d = parse_decision(raw)
    assert d["action"] == "hold"


def test_garbage_degrades_to_hold():
    d = parse_decision("no json here at all")
    assert d["action"] == "hold" and d["conviction"] == 0.0


def test_conviction_is_clamped():
    d = parse_decision('{"action": "buy", "conviction": 7.5, "reason": "x"}')
    assert d["conviction"] == 1.0
    d = parse_decision('{"action": "buy", "conviction": -3, "reason": "x"}')
    assert d["conviction"] == 0.0


def test_unknown_action_becomes_hold():
    d = parse_decision('{"action": "yolo", "conviction": 1.0, "reason": "x"}')
    assert d["action"] == "hold"


def test_non_dict_json_becomes_hold():
    assert parse_decision("[1, 2, 3]")["action"] == "hold"


def test_client_requires_config():
    with pytest.raises(LLMError):
        LLMClient(base_url=None, api_key=None, model=None)


def test_client_rejects_placeholder_credentials():
    # every non-empty placeholder defined by the client must count as unconfigured
    placeholders = [p for p in _PLACEHOLDER_KEYS if p]
    assert placeholders, "client should define at least one placeholder value"
    for ph in placeholders:
        with pytest.raises(LLMError):
            LLMClient(base_url="https://api.example.com/v1", api_key=ph, model="x")


def make_client(**kwargs):
    defaults = dict(base_url="https://api.example.com/v1", api_key="k", model="m")
    defaults.update(kwargs)
    return LLMClient(**defaults)


def test_payload_defaults_leave_reasoning_headroom():
    c = make_client()
    payload = c._build_payload("s", "u", 0.2, None)
    assert payload["max_tokens"] == 2000
    assert "thinking" not in payload  # unset = provider default, no vendor field


def test_payload_with_thinking_toggle():
    c = make_client(thinking="disabled")
    payload = c._build_payload("s", "u", 0.2, 500)
    assert payload["max_tokens"] == 500
    assert payload["thinking"] == {"type": "disabled"}

    # GLM-5.x effort levels map to the "effort" field (verified live)
    c = make_client(thinking="low")
    assert c._build_payload("s", "u", 0.2, None)["thinking"] == {"effort": "low"}
    c = make_client(thinking="max")
    assert c._build_payload("s", "u", 0.2, None)["thinking"] == {"effort": "max"}


def test_client_rejects_bad_thinking_value():
    with pytest.raises(LLMError):
        make_client(thinking="sometimes")


import json as _json
import time as _time
from urllib.error import HTTPError

import bot.llm.client as client_module


class FakeResponse:
    def __init__(self, body):
        self._body = _json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_chat_retries_on_rate_limit(monkeypatch):
    sleeps = []
    monkeypatch.setattr(_time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def fake_open(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPError(url, 429, "Too Many Requests", None, None)
        return FakeResponse({"choices": [{"message": {"content": "hello"}}]})

    monkeypatch.setattr(client_module, "safe_urlopen", fake_open)
    c = make_client(min_request_interval=0.0)
    assert c.chat("s", "u") == "hello"
    assert calls["n"] == 2
    assert 10.0 in sleeps  # first backoff applied


def test_chat_does_not_retry_other_errors(monkeypatch):
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_open(url, **kwargs):
        calls["n"] += 1
        raise HTTPError(url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(client_module, "safe_urlopen", fake_open)
    c = make_client(min_request_interval=0.0)
    with pytest.raises(LLMError):
        c.chat("s", "u")
    assert calls["n"] == 1


def test_chat_paces_requests(monkeypatch):
    sleeps = []
    monkeypatch.setattr(_time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        client_module, "safe_urlopen",
        lambda url, **kw: FakeResponse(
            {"choices": [{"message": {"content": "x"}}]}),
    )
    c = make_client(min_request_interval=8.0)
    c.chat("s", "u")                      # first call: no wait expected
    c.chat("s", "u")                      # second call: must pace ~8s
    pacing = [s for s in sleeps if 7.0 < s <= 8.0]
    assert pacing, f"expected pacing sleep, got {sleeps}"
