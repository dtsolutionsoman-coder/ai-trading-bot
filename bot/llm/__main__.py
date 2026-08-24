"""API-key self-test: `python -m bot.llm`

One tiny chat call (~$0.0006) to confirm the key, endpoint, and model work
before you start any bot. Exits 0 on success.
"""

from .client import LLMClient, LLMError


def main() -> int:
    try:
        client = LLMClient.from_env()
    except LLMError as exc:
        print(f"NOT CONFIGURED: {exc}")
        return 2

    print(f"configured: {client.model} @ {client.base_url} "
          f"(thinking={client.thinking or 'provider default'}, "
          f"max_tokens={client.max_tokens})")
    try:
        reply = client.chat(
            "You are a connectivity test. Reply with ONLY the JSON "
            '{"ok": true}',
            "test",
        )
    except LLMError as exc:
        print(f"FAILED: {exc}")
        return 1

    print(f"SUCCESS — model replied: {reply.strip()[:120]}")
    print("your key works; start the bots with start_ai.bat")
    return 0


raise SystemExit(main())
