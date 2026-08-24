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
    real_decisions = [d for d in decisions.values() if "probability" in d]

    return {
        "days": days,
        "fills": len(fills),
        "closed_trades": metrics.get("n_closed_trades", 0),
        "return_pct": metrics.get("total_return_pct", 0.0),
        "max_dd_pct": metrics.get("max_drawdown_pct", 0.0),
        "win_rate_pct": metrics.get("win_rate_pct", 0.0),
        "profit_factor": metrics.get("profit_factor"),
        "decisions": len(real_decisions),
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
              f"{s['closed_trades']} closed trades, {s['decisions']} LLM decisions")
        print(f"  return        {s['return_pct']:+.2f}%   "
              f"max drawdown -{s['max_dd_pct']:.2f}%")
        print(f"  win rate      {s['win_rate_pct']:.0f}%   "
              f"profit factor {pf_txt}")
        if s["decisions"]:
            print(f"  avg |edge|    {s['avg_abs_edge']:.3f} "
                  f"(how far GLM dares to disagree)")
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
