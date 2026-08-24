# Running the bots 24/7 on GitHub Actions — free, NO CARD

Everything runs on GitHub's free CI runners: every 15 minutes a workflow runs
one cycle of each bot (`--once`), commits the updated state files back to the
repo, and a **static dashboard on GitHub Pages** shows the snapshot. Free
forever on a **public** repo (unlimited minutes), no credit card, no server
to maintain. Your GLM key lives in GitHub **Secrets**, never in the repo.

Trade-offs vs a real VM: 15-minute granularity (fine — our bars are 15m),
occasional cron jitter, and the dashboard is a snapshot rather than live.

## Setup (~15 minutes, one time)

### 1. Create the public repo
1. Account on github.com → **New repository** → name it e.g. `ai-trading-bot`
   → visibility **Public** (required for free unlimited minutes) → create.

### 2. Add your LLM key as Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**,
four times:

| Secret name | Value |
|---|---|
| `LLM_BASE_URL` | `https://api.z.ai/api/paas/v4` |
| `LLM_API_KEY` | your key |
| `LLM_MODEL` | `glm-5.3` |
| `LLM_THINKING` | `low` |

### 3. Push the code (from the laptop, in Git Bash)
```bash
cd /c/Users/Graph/Documents/General/ai-trading-bot
git init
git add -A
git commit -m "AI trading bot — paper race"
git remote add origin https://github.com/YOUR-NAME/ai-trading-bot.git
git branch -M main
git push -u origin main
```
(`config.json` with your key is gitignored — it never leaves the laptop;
CI uses the Secrets instead. The `output/*.json` state files DO go up so the
books continue seamlessly — they contain paper-trading data only.)

### 4. Turn on the Pages dashboard
Repo → **Settings → Pages** → Source: **Deploy from a branch** → Branch:
`main`, folder `/ (root)` → Save.
Your dashboard is then at `https://YOUR-NAME.github.io/ai-trading-bot/`
(uses the `index.html` in the repo root; refreshes as CI commits state).

### 5. First run + verify
Repo → **Actions** tab → `bots` workflow → **Run workflow** button → watch
the run finish green. Then check:
- Actions log shows each bot's cycle output
- A `[skip ci]` "state update" commit appeared
- The Pages URL renders books

The schedule (`*/15 * * * *`) takes over from there automatically.

## Day-to-day

- **Watch**: the Pages dashboard URL, or the repo's commit history (every
  state change is a commit — a built-in audit trail).
- **Logs**: Actions tab → any run → expand steps.
- **Calibration meter**: it needs the repo checked out — run locally:
  `git pull && python -m bot.pol.report`.
- **Pause everything**: Actions tab → `bots` → `...` → **Disable workflow**.
- **Update code**: edit on the laptop, `git push`; the next scheduled run
  uses the new code automatically.

## Handoff from the laptop

Keep the laptop bots running until you've watched **two or three successful
scheduled runs** on GitHub, then close all the bot windows on the laptop —
otherwise both copies trade the same books independently. The pushed state
files mean the race continues from the exact same candles.

## Later: moving to a real VPS

When you outgrow 15-minute granularity (or want the live dashboard, signed
testnet orders, or WebSocket tick data), everything transfers: the same repo
plus `DEPLOY.md` (Oracle free with card, or any paid VPS — Hetzner ~$4/mo).
