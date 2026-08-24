"""Performance metrics from an equity curve and fill list.

All annualization uses a naive constant `bars_per_year` (default: 24*365 for
1h crypto bars). Pass the right value for your bar interval.
"""

from __future__ import annotations

import math
from typing import Iterable

from ..core.models import Fill


def compute_metrics(
    equity_curve: Iterable[tuple],
    fills: Iterable[Fill],
    starting_cash: float,
    bars_per_year: int = 24 * 365,
) -> dict:
    eq = [float(e) for _, e in equity_curve]
    fills = list(fills)

    if len(eq) < 2 or starting_cash <= 0:
        return _empty_metrics(starting_cash, eq, fills)

    rets = [eq[i + 1] / eq[i] - 1.0 for i in range(len(eq) - 1) if eq[i] > 0]
    total_return = eq[-1] / starting_cash - 1.0

    periods = max(1, len(eq) - 1)
    if eq[-1] > 0:
        ann_return = (eq[-1] / starting_cash) ** (bars_per_year / periods) - 1.0
        # keep absurd compounding artifacts from overflowing displays
        ann_return = min(ann_return, 1e6)
    else:
        ann_return = -1.0

    mean = sum(rets) / len(rets) if rets else 0.0
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) if len(rets) > 1 else 0.0
    sharpe = (mean / std) * math.sqrt(bars_per_year) if std > 0 else 0.0

    peak, max_dd = eq[0], 0.0
    for value in eq:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)

    closed = [f for f in fills if abs(f.realized) > 1e-12]
    wins = [f for f in closed if f.realized > 0]
    losses = [f for f in closed if f.realized < 0]
    gross_win = sum(f.realized for f in wins)
    gross_loss = -sum(f.realized for f in losses)
    profit_factor = None
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    elif gross_win > 0:
        profit_factor = None  # no losing trades; ratio undefined/infinite

    return {
        "starting_cash": starting_cash,
        "final_equity": eq[-1],
        "total_return_pct": 100.0 * total_return,
        "annualized_return_pct": 100.0 * ann_return,
        "sharpe": sharpe,
        "max_drawdown_pct": 100.0 * max_dd,
        "n_fills": len(fills),
        "n_closed_trades": len(closed),
        "win_rate_pct": (100.0 * len(wins) / len(closed)) if closed else 0.0,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": profit_factor,
        "total_fees": sum(f.fee for f in fills),
    }


def _empty_metrics(starting_cash: float, eq: list[float], fills: list[Fill]) -> dict:
    return {
        "starting_cash": starting_cash,
        "final_equity": eq[-1] if eq else starting_cash,
        "total_return_pct": 0.0,
        "annualized_return_pct": 0.0,
        "sharpe": 0.0,
        "max_drawdown_pct": 0.0,
        "n_fills": len(fills),
        "n_closed_trades": 0,
        "win_rate_pct": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "profit_factor": None,
        "total_fees": sum(f.fee for f in fills),
    }
