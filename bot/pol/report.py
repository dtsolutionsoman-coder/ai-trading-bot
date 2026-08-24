"""Polymarket calibration report — is the LLM's probability judgment any good?

    python -m bot.pol.report

Compares every stored decision against the market's final outcome (Brier
score: lower = better). The question that matters: does the model beat the
MARKET's own price as a probability estimate? If not, there is no edge and
no betting should happen — which the entry threshold already enforces.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..venues.polymarket import GammaClient

_STATE_PATH = Path("output/pol_state.json")


def score_decisions(decisions: dict, finals: dict[str, float]) -> dict:
    """Pure scoring. finals: {market_id: 1.0 or 0.0 once resolved}."""
    settled = [
        (d, finals[mid])
        for mid, d in decisions.items()
        if "probability" in d and finals.get(mid) in (0.0, 1.0)
    ]
    if not settled:
        return {"settled": 0}

    def brier(p, o):
        return (p - o) ** 2

    model = sum(brier(d["probability"], o) for d, o in settled)
    market = sum(brier(d.get("market_price", 0.5), o) for d, o in settled)
    closer = sum(
        1
        for d, o in settled
        if brier(d["probability"], o) < brier(d.get("market_price", 0.5), o)
    )
    n = len(settled)
    return {
        "settled": n,
        "model_brier": round(model / n, 4),
        "market_brier": round(market / n, 4),
        "model_closer_count": closer,
        "model_beats_market": (model / n) < (market / n),
    }


def main(argv: list[str] | None = None) -> int:
    if not _STATE_PATH.exists():
        print("no pol_state.json yet — run the pol bot first")
        return 1

    state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    decisions = state.get("decisions", {})
    real = {mid: d for mid, d in decisions.items() if "probability" in d}
    if not real:
        print("no LLM decisions recorded yet")
        return 0

    finals: dict[str, float] = {}
    open_rows: list[tuple[str, dict, float]] = []
    try:
        markets = {m.id: m for m in GammaClient().markets_by_ids(list(real))}
    except Exception as exc:
        print(f"warn: could not reach Polymarket ({exc}) — showing stored data only")
        markets = {}

    for mid, d in real.items():
        m = markets.get(mid)
        if m is None:
            continue
        if m.closed and m.yes_price in (0.0, 1.0):
            finals[mid] = m.yes_price
        else:
            open_rows.append((mid, d, m.yes_price))

    print(f"{len(real)} decisions on record "
          f"({len(finals)} resolved, {len(open_rows)} still open)\n")

    score = score_decisions(real, finals)
    if score["settled"]:
        verdict = "MODEL BEATS MARKET" if score["model_beats_market"] else \
                  "market still ahead"
        print(f"calibration on resolved markets (Brier, lower=better):")
        print(f"  model : {score['model_brier']}")
        print(f"  market: {score['market_brier']}")
        print(f"  model closer on {score['model_closer_count']}/{score['settled']}"
              f" -> {verdict}\n")

    if open_rows:
        print("open markets — decision vs current drift:")
        for _mid, d, now_px in open_rows[:12]:
            drift = now_px - d.get("market_price", 0)
            direction = "model's side strengthening" if (
                (d["probability"] > d.get("market_price", 0)) == (drift > 0)
                and abs(d["probability"] - d.get("market_price", 0)) > 0.02
            ) else "flat/no strong signal"
            print(f"  P={d['probability']:.2f} entry_px={d.get('market_price', 0):.2f} "
                  f"now={now_px:.2f} ({drift:+.2f}) — {d['question'][:48]} [{direction}]")

    fills = state.get("portfolio", {}).get("fills", [])
    if fills:
        realized = sum(f.get("realized", 0.0) for f in fills)
        print(f"\npaper entries: {len(fills)} fills, realized P&L ${realized:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
