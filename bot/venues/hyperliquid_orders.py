"""REAL order placement on Hyperliquid via the official Python SDK.

This is the ONLY module in the project that can move money — and only on the
TESTNET by default (fake funds). Requires:
  1. a wallet private key in the HL_PRIVATE_KEY environment variable
     (never in code, never committed);
  2. the caller to explicitly opt in (--confirm-live on the CLI).
The local portfolio keeps mirroring fills so the dashboard still works; on-chain
state is the source of truth for real balances.

Verified plumbing only — order placement itself needs a funded testnet wallet,
so treat first runs as an experiment with $1 of fake money.
"""

from __future__ import annotations

import os

from ..core.models import Fill, Order, Side

_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
_MAINNET_URL = "https://api.hyperliquid.xyz"

_SETUP_HELP = (
    "to place testnet orders: create an Ethereum-style wallet, connect it to "
    "https://app.hyperliquid-testnet.xyz, claim the free testnet USDC from the "
    "faucet, then set HL_PRIVATE_KEY to the wallet's private key "
    "(env var only — never hardcode it)"
)


class HyperliquidOrderError(RuntimeError):
    pass


def _fill_price(result, fallback: float) -> float:
    """Best-effort average fill price from an SDK order response."""
    statuses = None
    if isinstance(result, dict):
        try:
            statuses = result["response"]["data"]["statuses"]
        except (KeyError, TypeError):
            statuses = result.get("statuses")
    if isinstance(statuses, list):
        for status in statuses:
            if isinstance(status, dict) and "filled" in status:
                try:
                    return float(status["filled"].get("avgPx") or fallback)
                except (TypeError, ValueError):
                    return fallback
    return fallback


class HyperliquidOrderVenue:
    def __init__(
        self,
        network: str = "testnet",
        key_env: str = "HL_PRIVATE_KEY",
        slippage: float = 0.02,
    ):
        key = (os.environ.get(key_env) or "").strip()
        if not key:
            raise HyperliquidOrderError(f"missing {key_env} env var; {_SETUP_HELP}")
        if network not in ("testnet", "mainnet"):
            raise HyperliquidOrderError("network must be testnet or mainnet")

        # imported lazily so the rest of the project never needs the SDK
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info

        base_url = _TESTNET_URL if network == "testnet" else _MAINNET_URL
        wallet = Account.from_key(key)
        self.network = network
        self.wallet_address = wallet.address
        self.slippage = slippage
        self.info = Info(base_url=base_url, skip_ws=True)
        self.exchange = Exchange(wallet, base_url=base_url)

    def position(self, coin: str) -> float:
        """Current signed position size for a coin (from the exchange)."""
        state = self.info.user_state(self.wallet_address)
        for entry in state.get("assetPositions", []):
            pos = entry.get("position", {})
            if pos.get("coin") == coin:
                try:
                    return float(pos.get("szi") or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def submit_order(self, order: Order) -> Fill:
        current = self.position(order.symbol)
        signed = order.qty if order.side is Side.BUY else -order.qty
        target = current + signed
        reduces = current != 0 and (abs(target) < abs(current) or (target > 0) != (current > 0))

        try:
            if reduces:
                result = self.exchange.market_close(
                    order.symbol, order.qty, None, self.slippage
                )
            else:
                result = self.exchange.market_open(
                    order.symbol, order.side is Side.BUY, order.qty, None, self.slippage
                )
        except Exception as exc:  # SDK/network errors — surface, never swallow
            raise HyperliquidOrderError(f"order failed: {exc}") from exc

        price = _fill_price(result, order.price)
        return Fill(
            ts=order.ts,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=price,
            fee=0.0,  # real fees show up in userFills; approximated here
            reason=order.reason,
        )
