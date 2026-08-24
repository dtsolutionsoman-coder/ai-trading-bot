# AI Trading Bot — four bots, one engine, all paper-first

A modular, stdlib-only Python trading system inspired by the "AI builds a
trading bot" genre. Four bots share one core (portfolio, risk, strategies,
fills), differ only by venue:

1. **Hyperliquid live paper bot** — trades real testnet/mainnet candles on a
   local paper book (optionally REAL testnet orders with your own wallet)
2. **Polymarket bot** — LLM estimates true probabilities vs market odds,
   paper-bets the edge, settles at resolution
3. **Solana sniper** — paper-trades freshly listed tokens (DexScreener feed)
4. **Solana copy-trader** — mirrors followed wallets' balance changes on paper

Plus a **local web dashboard** (`python -m bot.dashboard`) showing every
book's equity curve, positions, fills, and AI decisions.

**👉 How-to: `USAGE.md` · GLM costs: `COSTING.md` · free 24/7 with NO card:
`GH_ACTIONS.md` · free VM (card): `DEPLOY.md`**

> ⚠️ **Honest disclaimer.** Educational software. Default mode touches no real
> money. Most retail trading bots lose money; a profitable paper run does not
> imply profitable live trading. Nothing here is financial advice.

## Roadmap status

| Phase | Module | Status |
|---|---|---|
| 1 | Backtester / simulator core | ✅ |
| 2a | Hyperliquid data + live paper loop | ✅ |
| 2b | Signed testnet orders (`--exec testnet`) | ✅ (plumbing; needs your wallet to verify) |
| 3 | Polymarket paper bot | ✅ |
| 4 | Solana sniper + copy paper bots | ✅ |
| — | Dashboard | ✅ |
| — | Market-data layer (funding/OI/volume collector, SQLite, LLM feature feed) | ✅ |
| — | Data-layer upgrades: WebSocket ticks, liquidations, whale tracking | ⏳ |
| — | Cloud deployment (Oracle free VM, systemd, one-script setup) | ✅ `DEPLOY.md` |

Phases 2–4 reuse this exact portfolio/risk/strategy stack — only the "venue" changes.

## Quickstart

```bash
# zero dependencies for the core; pytest is only for the test suite
python -m pytest                # run tests (offline)
python -m bot.backtest.run      # demo backtest on synthetic data, SMA-cross strategy
```

Demo with real (public) market data — downloads BTCUSDT 1h candles and caches to `data/`:

```bash
python -m bot.backtest.run --source binance --bars 1000
```

## Live paper trading (Hyperliquid testnet data)

```bash
python -m bot.live.run --strategy sma_cross --symbol BTC --interval 1h --network testnet
```

Polls Hyperliquid's public testnet API for closed candles and trades them on the
local paper portfolio through the same risk/strategy pipeline as the backtester.
Handy variants:

```bash
--interval 1m                     # fast bars for watching it work
--once                           # single poll then exit (cron-friendly)
--max-bars 5                     # stop after 5 new bars
--reset                          # discard saved state, start flat
--strategy llm_analyst           # AI brain (needs LLM_* env vars, see below)
--network mainnet                # real mainnet prices (still paper trading)
```

State persists across restarts (`output/live_state.json`): cash, positions,
fills, equity curve, and the last processed bar — restarts never re-trade old
bars. **Execution is paper-only; no code path can place a real order.**


Run the LLM analyst brain (needs any OpenAI-compatible API key):

```bash
export LLM_BASE_URL="https://api.deepseek.com/v1"   # or OpenAI, Anthropic-compat gateways, Kimi...
export LLM_API_KEY="sk-..."
export LLM_MODEL="deepseek-chat"
python -m bot.backtest.run --strategy llm_analyst --source binance
```

(On Windows CMD use `set X=Y` instead of `export`, or fill in `config.json` — see
`config.example.json`. Keys are read from env vars or `config.json`; `config.json` is
gitignored.)

Results print to console and are written to `output/` (equity curve CSV + summary JSON).

## Layout

```
bot/
  core/        models, portfolio, risk, data feeds, safe-network util
  strategies/  BarContext + SMA baseline + LLM analyst
  llm/         provider-agnostic chat client (OpenAI-compatible)
  backtest/    runner, metrics, CLI
  venues/      paper venue + Hyperliquid public market-data client
  live/        live poll loop + persistence + CLI
tests/         offline unit + end-to-end tests
```

## Safety properties (by design)

- **No real-money code paths exist yet.** Execution is simulated fills only.
- All outbound HTTP (market data, LLM calls) goes through `bot.core.net`, which enforces
  https/http only and refuses localhost/loopback/private/reserved hosts.
- Risk manager caps single-position notional, enforces per-position stop-losses, and halts
  new entries after the daily loss limit is hit (stop-losses still fire while halted).

## License

MIT — see `LICENSE`.
