"""State-of-the-race report.

    python -m bot.report

Reads every paper book committed by the bots and prints what the evidence
actually says — including how STRONG that evidence is. A few days of data
cannot crown a winner; this tool says so explicitly instead of letting a
lucky curve tell stories.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .backtest.metrics import compute_metrics
from .core.models import Fill, Side

BOOKS = [
    ("output/race_sma.json", "SMA control (15m)"),
    ("output/live_llm.json", "GLM AI (15m)"),
    ("output/live_carry.json", "Funding carry BTC (15m)"),
    ("output/live_carrycat.json", "Funding carry CASHCAT (15m)"),
    ("output/pol_state.json", "Polymarket"),
]


def _fills_from_dicts(rows: list[dict]) -> list[Fill]:
    out = []
    for f in rows:
        try:
            out.append(Fill(
                ts=datetime.fromisoformat(f["ts"]),
                symbol=f.get("symbol", "?"),
                side=Side(f.get("side", "buy")),
                qty=float(f.get("qty", 0.0)),
                price=float(f.get("price", 0.0)),
                fee=float(f.get("fee", 0.0)),
                realized=float(f.get("realized", 0.0)),
                reason=f.get("reason", ""),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def summarize(state: dict) -> dict:
    """One summary dict for any live/pol state file."""
    portfolio = state.get("portfolio", state)
    curve = [(ts, float(eq)) for ts, eq in portfolio.get("equity_curve", [])]
    fills = _fills_from_dicts(portfolio.get("fills", portfolio.get("recent_fills", [])))
    starting = float(portfolio.get("starting_cash", 0.0) or 0.0)

    metrics = compute_metrics(curve, fills, starting) if curve else {}
    days = 0.0
    if len(curve) >= 2:
        t0 = datetime.fromisoformat(curve[0][0])
        t1 = datetime.fromisoformat(curve[-1][0])
        days = max((t1 - t0).total_seconds() / 86_400, 0.0)

    decisions = state.get("decisions", {})
    # pol stores {market_id: {...}}, live books store a list of decision dicts;
    # only probability-bearing decisions (pol-style) count toward Brier stats
    if isinstance(decisions, dict):
        decision_rows = list(decisions.values())
    else:
        decision_rows = list(decisions)
    real_decisions = [
        d for d in decision_rows
        if isinstance(d, dict) and "probability" in d
    ]

    return {
        "days": days,
        "fills": len(fills),
        "closed_trades": metrics.get("n_closed_trades", 0),
        "return_pct": metrics.get("total_return_pct", 0.0),
        "max_dd_pct": metrics.get("max_drawdown_pct", 0.0),
        "win_rate_pct": metrics.get("win_rate_pct", 0.0),
        "profit_factor": metrics.get("profit_factor"),
        "decisions": len(real_decisions),
        "total_decisions": sum(1 for d in decision_rows if isinstance(d, dict)),
        "avg_abs_edge": (
            sum(abs(d["probability"] - d.get("market_price", 0.0))
                for d in real_decisions) / len(real_decisions)
            if real_decisions else 0.0
        ),
        "last_save": state.get("saved_at", ""),
    }


def evidence_strength(closed_trades: int) -> str:
    if closed_trades < 20:
        return ("TOO EARLY — numbers below are noise, not skill "
                f"({closed_trades} closed trades; need 20+ for a first read, "
                "100+ for a real verdict)")
    if closed_trades < 100:
        return (f"WEAK-DIRECTIONAL — {closed_trades} closed trades: "
                "interesting, not conclusive")
    return f"STATISTICAL — {closed_trades} closed trades: this is a real verdict"


def funding_accrued(state: dict, context_jsonl: str) -> float | None:
    """Estimated funding payments a book earned (paper equity MISSES these).

    Reconstructs the signed position over time from fills, walks the funding
    history, and accrues `-qty x mark x rate x dt` per snapshot (shorts
    receive positive funding). 15-minute snapshots, hourly rates -> dt=0.25h.
    """
    path = Path(context_jsonl)
    if not path.exists():
        return None
    portfolio = state.get("portfolio", state)
    symbol = state.get("symbol")
    if not symbol:
        return None
    fills = sorted(
        _fills_from_dicts(portfolio.get("fills", portfolio.get("recent_fills", []))),
        key=lambda f: f.ts,
    )
    if not fills:
        return 0.0

    events = []  # (ts_ms, qty_delta_signed, price)
    for f in fills:
        signed = f.qty if f.side is Side.BUY else -f.qty
        events.append((f.ts.timestamp() * 1000, signed, f.price))

    accrued = 0.0
    qty = 0.0
    i = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
                if rec.get("k") != "c":
                    continue
                v = rec["v"]
                if v[1] != symbol:
                    continue
                ts_ms, _coin, mark = int(v[0]), v[1], float(v[2] or 0.0)
                funding = float(v[4] or 0.0)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError,
                    ValueError):
                continue
            while i < len(events) and events[i][0] <= ts_ms:
                qty += events[i][1]
                i += 1
            if qty != 0.0 and mark > 0.0:
                accrued += -qty * mark * funding * 0.25  # 15min of hourly rate
    return accrued


def main(argv: list[str] | None = None) -> int:
    print("=" * 68)
    print("STATE OF THE RACE — paper evidence so far")
    print("=" * 68)
    any_book = False
    for path, name in BOOKS:
        p = Path(path)
        if not p.exists():
            print(f"\n{name}: no state file yet")
            continue
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"\n{name}: unreadable ({exc})")
            continue
        any_book = True
        s = summarize(state)
        pf = s["profit_factor"]
        pf_txt = "n/a" if pf is None else f"{pf:.2f}"
        print(f"\n{name}")
        print(f"  evidence      {s['days']:.1f} days, {s['fills']} fills, "
              f"{s['closed_trades']} closed trades, {s['total_decisions']} decisions "
              f"({s['decisions']} probability-scored)")
        print(f"  return        {s['return_pct']:+.2f}%   "
              f"max drawdown -{s['max_dd_pct']:.2f}%")
        print(f"  win rate      {s['win_rate_pct']:.0f}%   "
              f"profit factor {pf_txt}")
        if s["decisions"]:
            print(f"  avg |edge|    {s['avg_abs_edge']:.3f} "
                  f"(how far GLM dares to disagree)")
        if "carry" in path:
            funding = funding_accrued(state, "output/market_data_mainnet.jsonl")
            if funding is not None:
                print(f"  funding est.  {funding:+.2f} USD collected "
                      f"(NOT included in the equity curve above)")
        print(f"  last saved    {s['last_save'] or '?'}")
        print(f"  verdict       {evidence_strength(s['closed_trades'])}")

    if any_book:
        print("\n" + "-" * 68)
        print("projection math: expectancy per trade = win% × avg win − "
              "loss% × avg loss.")
        print("Only trust it AFTER the verdict line turns STATISTICAL — "
              "before that, luck dominates.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
