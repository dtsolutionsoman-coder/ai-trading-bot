# Costing plan — running the bots on GLM-5.3 (thinking off / high / max)

*Written 2026-08-24. Prices from [docs.z.ai pricing](https://docs.z.ai/guides/overview/pricing):
GLM-5.3 = **$1.40 / M input, $0.26 / M cached input, $4.40 / M output** (USD).
GLM-4.7-Flash = **free**. Verify on the pricing page before budgeting — prices change.*

## 1. Where tokens come from (measured from this repo's actual prompts)

| Call | Input tokens | Output tokens (thinking off) |
|---|---|---|
| Live-bot decision (`llm_analyst`, every 6th bar) | ~210 | ~60 |
| Polymarket probability estimate (once per market) | ~160 | ~50 |

Reasoning-effort tiers don't change the per-token price — they change **how
many output tokens** the model generates as thinking. Thinking tokens bill as
output. We measured our answer itself at ~50-60 tokens; the thinking budget is
the whole cost story:

| Mode | Assumed total output tokens/call | Notes |
|---|---|---|
| Thinking off | ~60 | plain JSON answer |
| "high" | ~1,500 | measured-style estimate for a small task |
| "max" | ~5,000 | same task, maximum reasoning budget |

**These two thinking numbers are assumptions** — actuals depend on z.ai's
effort implementation. Check your usage dashboard after the first week and
re-scale the tables below linearly.

Cost per call: `input×$1.40/M + output×$4.40/M`

| Mode | Live-bot call | Polymarket call |
|---|---|---|
| off | $0.00056 | $0.00044 |
| high | $0.0069 | $0.0068 |
| max | $0.0223 | $0.0222 |

## 2. Call volumes by usage pattern (defaults, 30-day month)

| Activity | Calls/month |
|---|---|
| Live bot, 1h bars (4 decisions/day) | 120 |
| Live bot, 15m bars (16/day) | 480 |
| Live bot, 5m bars (48/day) | 1,440 |
| Live bot, 1m bars (240/day) | 7,200 |
| Polymarket, ~10 new liquid markets/day | 300 |
| Polymarket, ~20/day (heavy news cycle) | 600 |
| One 1,000-bar LLM backtest | 167 per run |
| One 5,000-bar LLM backtest | 834 per run |

SMA strategy, sniper, copy-trader, dashboard: **zero** LLM calls.

## 3. Monthly cost — per component

| Component | off | high | max |
|---|---|---|---|
| Live bot 1h | $0.07 | $0.83 | $2.68 |
| Live bot 15m | $0.27 | $3.31 | $10.70 |
| Live bot 5m | $0.80 | $9.94 | $32.11 |
| Live bot 1m | $4.02 | $49.68 | $160.56 |
| Polymarket 10/day | $0.13 | $2.04 | $6.66 |
| Polymarket 20/day | $0.27 | $4.08 | $13.32 |
| Backtest 1k bars (per run) | $0.09 | $1.15 | $3.72 |
| Backtest 5k bars (per run) | $0.47 | $5.76 | $18.60 |

## 4. Total for "everything", three profiles

| Profile | Setup | off | high | max |
|---|---|---|---|---|
| **Standard** | 1h live + pol 10/day + 2 backtests/mo | **$0.39** | **$5.17** | **$16.78** |
| **Active** | 15m live + pol 20/day + 5 backtests/mo | **$1.01** | **$13.15** | **$42.63** |
| **Degen** | 1m loop 24/7 + pol 20/day + 10 backtests/mo | **$5.22** | **$65.28** | **$211.10** |

(USD; ×~7.2 for CNY. At today's rates: Standard ≈ ¥3/¥37/¥121,
Active ≈ ¥7/¥95/¥307, Degen ≈ ¥38/¥470/¥1,520.)

Upside not counted: the system prompt (~150 constant tokens) may hit the
cached-input rate ($0.26/M instead of $1.40/M), trimming input cost ~5× on
that portion. Rounded, it's a few percent — ignore it.

## 5. Recommended setup (best quality per dollar)

1. **Live trading loop → GLM-5.3 with thinking off** (`LLM_THINKING=disabled`).
   A 7-number market summary doesn't need 5,000 tokens of deliberation, and
   the answer is a one-line JSON. Cost: cents/month even at 15m bars.
2. **Polymarket brain → GLM-5.3 with reasoning on** — probability calibration
   is the one place extra thinking plausibly helps, and it's only ~300
   calls/month: **~$2/month at "high", ~$7 at "max"**.
3. **Backtest sweeps → GLM-4.7-Flash (free)** while tuning; re-run the final
   configuration once on GLM-5.3 to confirm decisions don't change.
4. Set a **spending cap** in the z.ai console (e.g. $10/month) so a runaway
   loop can never surprise you.

Env setup per terminal (each bot process can use its own model):

```bash
# terminal: live bot — cheap mode
export LLM_BASE_URL="https://api.z.ai/api/paas/v4"
export LLM_API_KEY="..."
export LLM_MODEL="glm-5.3"
export LLM_THINKING="disabled"

# terminal: polymarket — reasoning mode
export LLM_BASE_URL="https://api.z.ai/api/paas/v4"
export LLM_API_KEY="..."
export LLM_MODEL="glm-5.3"
export LLM_THINKING="enabled"
```

(The bot's client now defaults to `max_tokens=2000` so thinking tokens can't
crowd out the JSON answer — the old 400 cap would have made reasoning-mode
replies arrive empty and silently degrade every decision to "hold".)

## 6. How to verify real spend

- z.ai console → usage/billing dashboard shows token breakdown per day.
- The bot logs every decision (`output/*_state.json` → `decisions` /
  fill reasons), so calls-per-day is directly countable.
- Re-scale this plan: `monthly_cost = calls/day × 30 × per-call cost` where
  per-call cost comes from §1 with YOUR measured thinking tokens.
