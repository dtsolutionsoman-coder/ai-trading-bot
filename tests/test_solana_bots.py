from datetime import datetime, timedelta

import pytest

from bot.sol.runner import CopyConfig, CopyPaperRunner, SniperConfig, SniperPaperRunner
from bot.venues.dexscreener import TokenPair, best_solana_pair, parse_pair
from bot.venues.solana_rpc import parse_token_accounts

MINT = "FakeMintAddress1111111111111111111111111111"
PAIR_ADDR = "PairAddress111111111111111111111111111111"


def pair(price=0.001, liq=20_000.0, vol=10_000.0, address=PAIR_ADDR, token=MINT):
    return TokenPair(address, token, "MEME", price, liq, vol, None)


class FakeDex:
    def __init__(self, tokens, pairs):
        self.tokens = tokens      # list[str] token addresses
        self.pairs = pairs        # {token_address: TokenPair | None}

    def latest_solana_tokens(self, limit=30):
        return self.tokens[:limit]

    def token_pair(self, token_address):
        return self.pairs.get(token_address)


class FakeRpc:
    def __init__(self, snapshots):
        self.snapshots = snapshots  # list per successive call
        self.i = 0

    def wallet_tokens(self, wallet):
        snap = self.snapshots[min(self.i, len(self.snapshots) - 1)]
        self.i += 1
        return snap


# ---------- dexscreener parsing ----------

def test_parse_pair_and_best_solana():
    raw = {
        "chainId": "solana", "pairAddress": PAIR_ADDR,
        "baseToken": {"address": MINT, "symbol": "MEME"},
        "priceUsd": "0.0015", "liquidity": {"usd": 12_000.0},
        "volume": {"h24": 8_000.0},
    }
    p = parse_pair(raw, MINT)
    assert p is not None and p.price_usd == 0.0015 and p.liquidity_usd == 12_000.0

    other = dict(raw, pairAddress="X", liquidity={"usd": 50.0})
    best = best_solana_pair([other, raw], MINT)
    assert best.pair_address == PAIR_ADDR

    base_chain = dict(raw, chainId="base")
    assert best_solana_pair([base_chain], MINT) is None


def test_parse_token_accounts():
    inner_info = {
        "mint": MINT,
        "tokenAmount": {"uiAmount": 123.5},
    }
    account = {"data": {"parsed": {"info": inner_info}}}
    result = {"value": [{"account": account}]}
    assert parse_token_accounts(result) == {MINT: 123.5}
    assert parse_token_accounts(None) == {}
    assert parse_token_accounts({}) == {}


# ---------- sniper ----------

def make_sniper(tmp_path, dex, **cfg):
    config = SniperConfig(state_path=tmp_path / "sniper.json", **cfg)
    return SniperPaperRunner(dex, config, log=lambda *_: None)


def test_sniper_opens_position_on_fresh_token(tmp_path):
    dex = FakeDex([MINT], {MINT: pair()})
    runner = make_sniper(tmp_path, dex)
    s = runner.run_cycle()
    assert len(s["opened"]) == 1
    pos = runner.portfolio.positions[PAIR_ADDR]
    assert pos.qty == pytest.approx(50.0 / 0.001)
    assert PAIR_ADDR in runner.tracked
    assert MINT in runner.seen


def test_sniper_skips_illiquid(tmp_path):
    dex = FakeDex([MINT], {MINT: pair(liq=100.0, vol=10.0)})
    runner = make_sniper(tmp_path, dex)
    s = runner.run_cycle()
    assert s["opened"] == [] and s["skipped"] == 1
    assert MINT in runner.seen  # remembered so it isn't retried


def test_sniper_take_profit(tmp_path):
    dex = FakeDex([MINT], {MINT: pair(price=0.001)})
    runner = make_sniper(tmp_path, dex)
    runner.run_cycle()
    # price doubles -> +100% >= 80% take-profit
    dex.pairs[MINT] = pair(price=0.002)
    s = runner.run_cycle()
    assert any("take-profit" in c["reason"] for c in s["closed"])
    assert runner.portfolio.positions[PAIR_ADDR].qty == 0
    assert PAIR_ADDR not in runner.tracked


def test_sniper_exit_when_price_data_vanishes(tmp_path):
    dex = FakeDex([MINT], {MINT: pair()})
    runner = make_sniper(tmp_path, dex)
    runner.run_cycle()
    dex.pairs[MINT] = None
    s = runner.run_cycle()
    assert len(s["closed"]) == 1
    assert "stopped" in s["closed"][0]["reason"]


def test_sniper_max_age_exit(tmp_path):
    dex = FakeDex([MINT], {MINT: pair()})
    runner = make_sniper(tmp_path, dex)
    runner.run_cycle()
    old = datetime.now() - timedelta(hours=30)
    runner.tracked[PAIR_ADDR]["opened_at"] = old.isoformat(timespec="seconds")
    s = runner.run_cycle()
    assert any("max age" in c["reason"] for c in s["closed"])


def test_sniper_state_roundtrip(tmp_path):
    dex = FakeDex([MINT], {MINT: pair()})
    runner = make_sniper(tmp_path, dex)
    runner.run_cycle()

    runner2 = make_sniper(tmp_path, dex)
    assert runner2.load_state() is True
    assert runner2.tracked == runner.tracked
    assert runner2.seen == runner.seen


# ---------- copy ----------

def make_copy(tmp_path, rpc, dex, **cfg):
    config = CopyConfig(state_path=tmp_path / "copy.json", **cfg)
    return CopyPaperRunner(rpc, dex, config, log=lambda *_: None)


def test_copy_mirrors_wallet_buy_then_sell(tmp_path):
    rpc = FakeRpc([
        {},             # baseline: empty wallet
        {MINT: 100.0},  # wallet bought 100 tokens
        {MINT: 40.0},   # wallet sold 60
    ])
    dex = FakeDex([], {MINT: pair(price=2.0, address=PAIR_ADDR)})
    runner = make_copy(tmp_path, rpc, dex)
    instrument = runner._instrument(MINT)

    s0 = runner.run_cycle(["WhaleWallet1"])
    assert s0["mirrored"] == []  # baseline only

    s1 = runner.run_cycle(["WhaleWallet1"])
    assert len(s1["mirrored"]) == 1 and s1["mirrored"][0]["side"] == "buy"
    # notional = min(cap 50, 100 tokens * $2) = $50 -> 25 tokens
    assert runner.portfolio.positions[instrument].qty == pytest.approx(25.0)

    s2 = runner.run_cycle(["WhaleWallet1"])
    assert any(m["side"] == "sell" for m in s2["mirrored"])
    # wallet cut 60% -> we sell 60% of 25 = 15
    assert runner.portfolio.positions[instrument].qty == pytest.approx(10.0)


def test_copy_skips_stablecoins_and_sol(tmp_path):
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    wsol = "So11111111111111111111111111111111111111112"
    rpc = FakeRpc([{usdc: 5000.0, wsol: 12.0}])
    dex = FakeDex([], {})
    runner = make_copy(tmp_path, rpc, dex)
    s = runner.run_cycle(["W1"])
    assert s["mirrored"] == []
    assert runner.portfolio.fills == []


def test_copy_first_snapshot_is_baseline(tmp_path):
    # first-ever snapshot must NOT be treated as buys — it's the starting point
    rpc = FakeRpc([{MINT: 100.0}])
    dex = FakeDex([], {MINT: pair(price=2.0)})
    runner = make_copy(tmp_path, rpc, dex)
    s = runner.run_cycle(["W1"])
    assert s["mirrored"] == []
    assert runner.snapshots["W1"] == {MINT: 100.0}
