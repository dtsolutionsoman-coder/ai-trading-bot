# How to use everything

You have **four bots** and a **dashboard**, all sharing one engine. Everything
runs on **paper money by default** — no code path can touch real funds unless
you explicitly set up testnet signing (2.6) and later mainnet (not built).

**Moving to free 24/7 cloud (Oracle Always Free VM): see `DEPLOY.md` —
one script (`deploy/setup.sh`) installs every bot as an auto-restarting
systemd service.**

```
┌─────────────┐   ┌──────────────────────────────────────────────┐
│  DASHBOARD  │←──│  output/*.json state files                    │
│  :8787      │   │  live_state / pol_state / sol_*_state         │
└─────────────┘   └──────────────┬───────────────────────────────┘
                                 │ read
┌────────────────┬───────────────┴────────┬──────────────────────┐
│ HYPERLIQUID    │ POLYMARKET             │ SOLANA               │
│ live paper bot │ LLM-vs-odds paper bot  │ sniper + copy paper  │
└────────────────┴────────────────────────┴──────────────────────┘
         all share: portfolio · risk manager · paper fills · backtester
```

## 0. What works with zero setup (right now)

```bash
cd ai-trading-bot

python -m pytest                        # 90 tests
python -m bot.backtest.run              # backtest on synthetic data
python -m bot.backtest.run --source binance       # real BTC history
python -m bot.live.run                  # paper-trade Hyperliquid testnet data
python -m bot.live.run --interval 1m    # fast version (bar every minute)
python -m bot.pol.run --once            # Polymarket scan (exits only, no LLM)
python -m bot.sol.run --mode sniper --once        # Solana new-listing scan
python -m bot.dashboard                 # web dashboard at http://127.0.0.1:8787
```

## 1. What YOU need to do (the only required steps)

**Nothing is required to paper-trade.** Two optional unlocks:

### a) LLM brain (needed for `llm_analyst` + Polymarket entries)
Any OpenAI-compatible API works: GLM (Zhipu/z.ai), DeepSeek, Kimi, OpenAI...

**GLM (verified compatible with this bot):**
```bash
export LLM_BASE_URL="https://api.z.ai/api/paas/v4"          # international (USD)
# or: https://open.bigmodel.cn/api/paas/v4                   # China (CNY)
export LLM_API_KEY="<your-open-platform-api-key>"
export LLM_MODEL="glm-4-flash"        # cheap tier for testing; bigger model
                                       # for better probability estimates
```
Note: the GLM *coding plan* subscription is licensed for coding tools, not for
app backends — use an open-platform API key (pay-as-you-go, see §1.a.1).

**DeepSeek example:**
```bash
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_API_KEY="sk-..."
export LLM_MODEL="deepseek-chat"
```

or copy `config.example.json` → `config.json` and fill the `llm` section
(`config.json` is gitignored). Windows CMD: use `set X=Y` per window.

**What it costs (order-of-magnitude):** each bot decision uses ~350 input +
~100 output tokens. At 1h bars that's ~4 decisions/day ≈ under ¥1/month even
on flagship models; a 24/7 1m loop ≈ 2.5M input tokens/month ≈ a few ¥ on
flash tiers, ¥15-20 on flagship; each 1000-bar LLM backtest ≈ 166 calls ≈
pennies. Check current per-million-token prices on your platform's pricing
page — flash-tier models are often free or near-free.

Then: `python -m bot.backtest.run --strategy llm_analyst --source binance`
and `python -m bot.pol.run --once` start making AI decisions.

### b) Hyperliquid testnet wallet (needed only for REAL testnet orders)
1. Create a fresh Ethereum-style wallet (e.g. a new MetaMask account —
   use a dedicated account for this, not your main one).
2. Go to `https://app.hyperliquid-testnet.xyz`, connect the wallet.
3. Use the testnet FAUCET there to get free fake USDC.
4. `export HL_PRIVATE_KEY="your-wallet-private-key"` (env var only).
5. Run: `python -m bot.live.run --exec testnet --confirm-live`
   (also set `--network testnet` for matching data).

The private key stays in the environment — never in code or committed files.

## 2. Running each bot

### Hyperliquid live paper bot
```bash
python -m bot.live.run --strategy sma_cross --symbol BTC --interval 1h
python -m bot.live.run --strategy llm_analyst --interval 15m --network mainnet
python -m bot.live.run --once                      # single poll (cron mode)
python -m bot.live.run --reset                      # start flat, wipe state
python -m bot.live.run --strategy llm_analyst --interval 1h \
    --context-db output/market_data.db              # enriched brain (see below)
```
- Waits for each candle to CLOSE, then trades it through stops→strategy→risk.
- `Ctrl+C` anytime: state saves automatically; restart resumes.
- Risk defaults: max 25% equity per position, 5% stop-loss, 4% daily halt.
- Symbols are Hyperliquid coin names: BTC, ETH, SOL, HYPE...

### Market-data collector (your data API, accumulating history)
```bash
python -m bot.data.run --coins BTC,ETH,SOL --once    # single collection cycle
python -m bot.data.run --coins BTC,ETH,SOL --loop    # 24/7 collector (60s)
python -m bot.data.run --coins BTC,ETH --network mainnet --loop
```
- Records per-coin funding, open interest, 24h volume, and premium every
  cycle, plus the hourly funding archive, into `output/market_data.db`
  (SQLite; WAL mode so collectors/readers can run simultaneously).
- Feed it to the trading brain with `--context-db`: LLM decisions then also
  see `funding_ann_pct`, `oi_change_24h_pct`, `day_volume_musd`,
  `perp_premium_bps` (OI change needs ~24h of collected history first).
- The longer it runs, the richer the features — leave it running in its own
  terminal forever.

### Polymarket bot (AI vs the odds)
```bash
python -m bot.pol.run --once            # scan top markets, manage positions
python -m bot.pol.run --loop            # continuous (60s cycles)
python -m bot.pol.run --edge 0.15 --cap 50   # pickier, smaller bets
```
- For each liquid binary market the LLM estimates P(YES); if it differs from
  the market price by ≥ `--edge`, it paper-bets the cheap side ($`--cap` max).
- Exits: +0.25/share take-profit, −0.15 stop, or settlement at $1.00/$0.00.
- Each market gets ONE decision ever (logged in state; shown on dashboard).

### Solana sniper (paper)
```bash
python -m bot.sol.run --mode sniper --once
python -m bot.sol.run --mode sniper --loop            # 90s cycles
```
- Watches DexScreener's newest Solana listings; paper-buys $50 of tokens with
  ≥ $5k liquidity and ≥ $3k 24h volume; exits +80% / −35% / 24h age.
- DexScreener is slow/rate-limited — timeouts are normal and handled; the bot
  skips what it can't fetch and tries the next batch.

### Solana copy-trader (paper)
```bash
python -m bot.sol.run --mode copy --wallet <PUBKEY> --once
python -m bot.sol.run --mode copy --wallet <A> --wallet <B> --loop
```
- Snapshots the wallet's SPL tokens via public RPC once per cycle; the FIRST
  run only stores a baseline (it can't know when old tokens were bought).
  From the second cycle on, balance increases → paper-buy, cuts → paper-sell.
- Poll gently (≥ 60s, few wallets) — public RPC is rate-limited.
- Honest limitation vs "real" copy bots: polling sees balances, not trades —
  tokens flipped within one cycle are invisible, and mirror prices are
  "current", not the whale's fill price.

### Dashboard
```bash
python -m bot.dashboard                # http://127.0.0.1:8787
```
Left sidebar lists every bot book + past backtests. Main panel: equity card,
equity curve chart, open positions, recent fills with reasons, Polymarket LLM
decisions, sniper watchlist. Auto-refreshes every 5 seconds. Local-only by
design (binds 127.0.0.1) — nobody outside your machine can see it.

## 3. Suggested daily routine

1. Terminal 1: `python -m bot.dashboard`
2. Terminal 2: `python -m bot.live.run --strategy llm_analyst --interval 1h`
3. Terminal 3: `python -m bot.pol.run --loop`
4. Once a day: `python -m bot.sol.run --mode sniper --once`
5. Watch the dashboard, not the terminals.

Or schedule the `--once` variants (Windows Task Scheduler → run hourly).

## 4. State files & resetting

Everything persists in `output/`:
| File | Bot |
|---|---|
| `live_state.json` | Hyperliquid paper |
| `pol_state.json` | Polymarket paper |
| `sol_sniper_state.json` / `sol_copy_state.json` | Solana bots |
| `backtest_*_summary.json` | Backtest reports |

Wipe any book with its CLI `--reset` flag (or delete the file).

## 5. Reading the numbers honestly

- Paper P&L assumes fills at price ± slippage (2–3%) and ignores things real
  markets punish you with: failed transactions, front-running, thin books,
  memes rug-pulling to zero between polls.
- A profitable paper month is necessary, not sufficient. If you ever consider
  real money: run testnet signing (1.b) first, then start with amounts you can
  lose entirely. Most retail bots lose money — plan for that being yours too.

## 6. Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| `no new closed 1h bar yet` | Normal — next bar closes at the top of the hour. |
| `LLM not configured` | Set `LLM_*` env vars or `config.json` (§1.a). |
| DexScreener timeouts | Normal; the bot retries next cycle. |
| `missing HL_PRIVATE_KEY` | You asked for `--exec testnet` without §1.b setup. |
| State looks stale | Check the sidebar's timestamp; bots write after every bar/cycle. |
