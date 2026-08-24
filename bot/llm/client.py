"""Provider-agnostic LLM chat client (OpenAI-compatible /chat/completions).

Works with OpenAI, DeepSeek, Kimi/Moonshot, and any compatible gateway.
Credentials come from env vars (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL) or
config.json — env wins. All requests go through the safe-open network guard.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError

from ..core.net import safe_urlopen

_CONFIG_FILE = Path("config.json")
_PLACEHOLDER_KEYS = {"", "PUT-YOUR-KEY-HERE"}

# GLM-5.x accepts effort levels (low/high/max, always-on thinking);
# GLM-4.x accepts enabled/disabled. Both spellings are passed through.
_THINKING_MODES = {"low", "high", "max", "enabled", "disabled"}

_RATE_LIMIT_CODE = 429
_BACKOFF_SECONDS = (10.0, 30.0, 60.0)


class LLMError(RuntimeError):
    """Raised for configuration problems or failed/unreadable API calls."""


def _load_config_file() -> dict:
    try:
        with _CONFIG_FILE.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise LLMError(f"could not read {_CONFIG_FILE}: {exc}") from exc


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        max_tokens: int = 2000,
        thinking: str | None = None,
        min_request_interval: float = 8.0,
    ):
        if not base_url or not api_key or api_key in _PLACEHOLDER_KEYS or not model:
            raise LLMError(
                "LLM is not configured. Set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL "
                "env vars or fill in the llm section of config.json "
                "(see config.example.json)."
            )
        if thinking is not None and thinking not in _THINKING_MODES:
            raise LLMError(
                "thinking must be one of " + ", ".join(sorted(_THINKING_MODES))
                + ", or unset"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # default leaves room for reasoning models whose thinking tokens count
        # against the output budget (e.g. GLM high/max efforts)
        self.max_tokens = max_tokens
        self.thinking = thinking
        # pacing: thinking-model calls are slow and rate limits are easy to
        # trip when a bot evaluates several markets in one cycle
        self.min_request_interval = max(float(min_request_interval), 0.0)
        self._last_request_at = 0.0

    @classmethod
    def from_env(cls) -> "LLMClient":
        file_cfg = _load_config_file().get("llm", {})
        base_url = os.environ.get("LLM_BASE_URL") or file_cfg.get("base_url")
        api_key = os.environ.get("LLM_API_KEY") or file_cfg.get("api_key")
        model = os.environ.get("LLM_MODEL") or file_cfg.get("model")

        max_tokens = file_cfg.get("max_tokens")
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS") or max_tokens or 2000)

        thinking = os.environ.get("LLM_THINKING") or file_cfg.get("thinking")
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            thinking=thinking,
        )

    def _build_payload(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int | None,
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        if self.thinking is not None:
            # GLM-5.x effort levels ride in {"thinking": {"effort": ...}};
            # GLM-4.x on/off rides in {"thinking": {"type": ...}}
            if self.thinking in ("low", "high", "max"):
                payload["thinking"] = {"effort": self.thinking}
            else:
                payload["thinking"] = {"type": self.thinking}
        return payload

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = json.dumps(
            self._build_payload(system, user, temperature, max_tokens)
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "ai-trading-bot/0.1",
        }

        # pace ourselves, then retry with backoff on rate limits
        wait = self.min_request_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

        last_error: LLMError | None = None
        for attempt, backoff in enumerate((0.0,) + _BACKOFF_SECONDS):
            if backoff:
                time.sleep(backoff)
            self._last_request_at = time.monotonic()
            try:
                with safe_urlopen(url, timeout=self.timeout, data=payload,
                                  headers=headers) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except HTTPError as exc:
                detail = exc.read()[:300].decode("utf-8", "replace") if exc.fp else ""
                last_error = LLMError(f"LLM API HTTP {exc.code}: {detail}")
                if exc.code != _RATE_LIMIT_CODE or attempt == len(_BACKOFF_SECONDS):
                    raise last_error from exc
            except (URLError, OSError, ValueError) as exc:
                raise LLMError(f"LLM request failed: {exc}") from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise last_error or LLMError("LLM request failed")

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {body!r:.300}") from exc
