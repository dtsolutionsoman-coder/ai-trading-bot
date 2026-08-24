"""Live loop: poll market data, act on each newly CLOSED bar.

Uses the exact same pipeline as the backtester (stops -> strategy -> risk ->
fill), so a strategy that works in backtest behaves identically live. State
(cash, positions, fills, equity curve) persists to JSON so restarts continue
where the bot left off. Execution goes through the paper venue — no real
orders can be placed from this module.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.models import Bar, Fill, Order, Side
from ..core.portfolio import Portfolio, Position
from ..core.risk import RiskConfig, RiskManager
from ..strategies.base import BarContext, Strategy
from ..venues.base import ExecutionVenue, MarketDataClient

_MAX_STATE_FILLS = 200  # keep the state file small; older fills are dropped
_MAX_STATE_POINTS = 2000


@dataclass
class LiveConfig:
    symbol: str = "BTC"
    interval: str = "1h"
    poll_seconds: float = 30.0
    starting_cash: float = 10_000.0
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    risk: RiskConfig = field(default_factory=RiskConfig)
    state_path: Path = field(default_factory=lambda: Path("output/live_state.json"))
    warmup_bars: int = 500  # history fetched at startup to warm indicators


class LiveRunner:
    def __init__(
        self,
        data_client: MarketDataClient,
        venue: ExecutionVenue,
        strategy: Strategy,
        config: LiveConfig,
        log=print,
    ):
        self.data_client = data_client
        self.venue = venue
        self.strategy = strategy
        self.config = config
        self.log = log

        self.portfolio = Portfolio(config.starting_cash)
        # The paper venue carries its own portfolio; adopt it so fills and
        # equity marks hit the SAME book (one source of truth).
        venue_portfolio = getattr(self.venue, "portfolio", None)
        if isinstance(venue_portfolio, Portfolio):
            self.portfolio = venue_portfolio
        self.risk = RiskManager(config.risk)
        self.history: list[Bar] = []
        self.last_bar_ts: datetime | None = None
        self.bars_processed = 0
        self._last_day = None

    # ----- state persistence -------------------------------------------------

    def state_file_exists(self) -> bool:
        return self.config.state_path.exists()

    def load_state(self) -> bool:
        """Restore portfolio + progress. Returns False if no state exists."""
        path = self.config.state_path
        if not path.exists():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))

        # mutate in place: the venue may hold a reference to this portfolio
        p = self.portfolio
        p.starting_cash = float(raw["starting_cash"])
        p.cash = float(raw["cash"])
        p.positions = {
            sym: Position(
                symbol=sym,
                qty=float(p_["qty"]),
                avg_price=float(p_["avg_price"]),
                realized_pnl=float(p_["realized_pnl"]),
            )
            for sym, p_ in raw.get("positions", {}).items()
        }
        p.fills = [
            Fill(
                ts=datetime.fromisoformat(f["ts"]),
                symbol=self.config.symbol,
                side=Side(f["side"]),
                qty=float(f["qty"]),
                price=float(f["price"]),
                fee=float(f["fee"]),
                realized=float(f["realized"]),
                reason=f.get("reason", ""),
            )
            for f in raw.get("recent_fills", [])
        ]
        p.equity_curve = [
            (datetime.fromisoformat(ts), float(eq))
            for ts, eq in raw.get("equity_curve", [])
        ]
        self.last_bar_ts = (
            datetime.fromisoformat(raw["last_bar_ts"]) if raw.get("last_bar_ts") else None
        )
        self.bars_processed = int(raw.get("bars_processed", 0))

        decisions = raw.get("decisions")
        if isinstance(decisions, list) and isinstance(
            getattr(self.strategy, "decisions", None), list
        ):
            self.strategy.decisions = decisions
        usage = raw.get("llm_usage")
        client = getattr(self.strategy, "client", None)
        if isinstance(usage, dict) and isinstance(getattr(client, "usage", None), dict):
            client.usage.update(usage)
        return True

    def save_state(self) -> None:
        path = self.config.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "symbol": self.config.symbol,
            "interval": self.config.interval,
        }
        decisions = getattr(self.strategy, "decisions", None)
        if isinstance(decisions, list):
            payload["decisions"] = decisions[-500:]
        client = getattr(self.strategy, "client", None)
        usage = getattr(client, "usage", None)
        if isinstance(usage, dict):
            payload["llm_usage"] = dict(usage)
        payload.update({
            "cash": self.portfolio.cash,
            "positions": {
                sym: {
                    "qty": p.qty,
                    "avg_price": p.avg_price,
                    "realized_pnl": p.realized_pnl,
                }
                for sym, p in self.portfolio.positions.items()
            },
            "recent_fills": [
                {
                    "ts": f.ts.isoformat(),
                    "side": f.side.value,
                    "qty": f.qty,
                    "price": f.price,
                    "fee": f.fee,
                    "realized": f.realized,
                    "reason": f.reason,
                }
                for f in self.portfolio.fills[-_MAX_STATE_FILLS:]
            ],
            "equity_curve": [
                [ts.isoformat(), round(eq, 2)]
                for ts, eq in self.portfolio.equity_curve[-_MAX_STATE_POINTS:]
            ],
            "last_bar_ts": self.last_bar_ts.isoformat() if self.last_bar_ts else None,
            "bars_processed": self.bars_processed,
            "starting_cash": self.portfolio.starting_cash,
        })
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ----- trading pipeline --------------------------------------------------

    def bootstrap(self) -> int:
        """Fetch history to warm indicators. Never trades on historical bars.

        The full fetched window becomes `history` (indicator context), and
        everything up to and including `last_bar_ts` is treated as already
        processed — on a fresh start that means the whole warmup window.
        """
        bars = self.data_client.fetch_bars(
            self.config.symbol, self.config.interval, limit=self.config.warmup_bars
        )
        self.history = bars
        if bars and self.last_bar_ts is None:
            self.last_bar_ts = bars[-1].ts
        return len(bars)

    def _new_closed_bars(self) -> list[Bar]:
        bars = self.data_client.fetch_bars(
            self.config.symbol, self.config.interval, limit=self.config.warmup_bars
        )
        if self.last_bar_ts is not None:
            bars = [b for b in bars if b.ts > self.last_bar_ts]
        return bars

    def process_bar(self, bar: Bar) -> list[Order]:
        """One bar through the full pipeline. Returns orders that got filled."""
        prices = {self.config.symbol: bar.close}
        equity = self.portfolio.equity(prices)

        day = bar.ts.date()
        if self._last_day != day:
            self.risk.on_new_day(equity)
            self._last_day = day
        else:
            self.risk.check_halt(equity)

        filled: list[Order] = []
        for order in self.risk.enforce_stops(bar.ts, self.portfolio.positions, prices):
            self.venue.submit_order(order)
            filled.append(order)

        self.history.append(bar)
        if not self.risk.halted:
            ctx = BarContext(
                ts=bar.ts,
                symbol=self.config.symbol,
                bar=bar,
                history=self.history,
                portfolio=self.portfolio,
                equity=equity,
            )
            for order in self.strategy.on_bar(ctx):
                pos = self.portfolio.positions.get(order.symbol)
                current_qty = pos.qty if pos else 0.0
                checked = self.risk.check_order(order, equity, current_qty=current_qty)
                if checked is not None:
                    self.venue.submit_order(checked)
                    filled.append(checked)

        self.portfolio.mark(bar.ts, prices)
        self.last_bar_ts = bar.ts
        self.bars_processed += 1
        return filled

    # ----- loops ---------------------------------------------------------------

    def run_one_cycle(self) -> int:
        """Poll once; process any newly closed bars. Returns bars processed."""
        new_bars = self._new_closed_bars()
        count = 0
        for bar in new_bars:
            filled = self.process_bar(bar)
            pos = self.portfolio.positions.get(self.config.symbol)
            pos_qty = pos.qty if pos else 0.0
            equity = self.portfolio.equity({self.config.symbol: bar.close})
            self.log(
                f"[{bar.ts}] {self.config.symbol} {self.config.interval} "
                f"close={bar.close:.2f} equity=${equity:,.2f} pos={pos_qty:+.6f}"
            )
            for order in filled:
                self.log(
                    f"    -> {order.side.value.upper()} {order.qty:.6f} @ "
                    f"{order.price:.2f}  ({order.reason})"
                )
            count += 1
        if count == 0:
            self.log(
                f"[{datetime.now():%H:%M:%S}] no new closed "
                f"{self.config.interval} bar yet; next poll in "
                f"{self.config.poll_seconds:.0f}s"
            )
        else:
            self.save_state()
        return count

    def run(self, max_bars: int | None = None) -> int:
        """Poll loop until max_bars new bars are processed or Ctrl+C."""
        processed = 0
        try:
            while max_bars is None or processed < max_bars:
                processed += self.run_one_cycle()
                if max_bars is not None and processed >= max_bars:
                    break
                time.sleep(max(self.config.poll_seconds, 0.0))
        except KeyboardInterrupt:
            self.log("interrupted — saving state and exiting")
        finally:
            self.save_state()
        return processed
