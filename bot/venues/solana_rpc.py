"""Minimal Solana JSON-RPC client (public mainnet endpoint, no API key).

Used by the copy-trade paper bot to snapshot a wallet's SPL token balances
and mirror deltas on paper. Public RPC is rate-limited: poll gently
(one call per wallet per minute is fine).
"""

from __future__ import annotations

import json

from ..core.net import safe_urlopen

_HOST = "api.mainnet-beta.solana.com"
_URL = f"https://{_HOST}"

_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# cash-equivalent mints we never mirror as "trades"
SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",  # wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


class SolanaRpcError(RuntimeError):
    pass


def parse_token_accounts(result: dict | None) -> dict[str, float]:
    """RPC result of getTokenAccountsByOwner (jsonParsed) -> {mint: uiAmount}."""
    out: dict[str, float] = {}
    if not isinstance(result, dict):
        return out
    for entry in result.get("value", []):
        parsed = (entry.get("account") or {}).get("data", {}).get("parsed", {})
        info = parsed.get("info", {})
        mint = info.get("mint")
        amount = (info.get("tokenAmount") or {}).get("uiAmount")
        if isinstance(mint, str) and isinstance(amount, (int, float)):
            out[mint] = float(amount)
    return out


class SolanaRpcClient:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self._id = 0

    def _call(self, method: str, params: list) -> dict:
        self._id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        ).encode("utf-8")
        with safe_urlopen(
            _URL,
            timeout=self.timeout,
            data=body,
            headers={"Content-Type": "application/json"},
            allowed_hosts={_HOST},
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if "error" in payload:
            raise SolanaRpcError(str(payload["error"])[:200])
        return payload.get("result")

    def health(self) -> str:
        return str(self._call("getHealth", []))

    def wallet_tokens(self, wallet_address: str) -> dict[str, float]:
        result = self._call(
            "getTokenAccountsByOwner",
            [wallet_address, {"programId": _TOKEN_PROGRAM}, {"encoding": "jsonParsed"}],
        )
        return parse_token_accounts(result)
