# Deploying to a free cloud VM (Oracle Always Free)

Goal: run all bots 24/7 without your laptop. **Oracle Cloud's Always Free tier**
gives a real VM that never sleeps and never bills (card is only for identity
verification). GCP's e2-micro is the fallback (also forever-free, 1GB RAM —
tight but workable). Free tiers on Render/Railway/Heroku-style platforms do
NOT fit: they sleep or lack background workers.

## 1. Create the VM (~10 min, one time)

1. Sign up at `console.oracle.com` (card needed, not charged for Always Free).
   **Region is fixed at signup** — pick your nearest; if A1 capacity is scarce
   there later, retry or fall back to the smaller E2.1.Micro shape.
2. Compute → Instances → Create:
   - Name: `aitb`
   - Image: **Ubuntu 22.04+** (canonical)
   - Shape: **VM.Standard.A1.Flex**, 2 OCPUs / 4 GB (free allowance goes to 4/24)
   - SSH key: "Generate key pair", save BOTH files somewhere safe
   - Leave networking defaults (only port 22/SSH open — the dashboard stays
     tunnel-only by design)
3. Note the **Public IP** when it's running.

If signup or A1 capacity fails: create a `VM.Standard.E2.1.Micro` (1GB) instead
— also always-free — everything below is identical.

## 2. Copy the project + your current books

From Git Bash **on the laptop** (this carries your config.json with the GLM
key and every paper book's state — the race continues seamlessly):

```bash
cd /c/Users/Graph/Documents/General
scp -i /path/to/your-key -r ai-trading-bot ubuntu@PUBLIC_IP:~/
```

(Windows `scp` may need `scp -i C:/path/to/key -r ...`. If your key has
loose permissions: `chmod 600` it inside `~/.ssh` on the server, or use
`ssh -o IdentitiesOnly=yes`.)

## 3. Install and start (on the server)

```bash
ssh -i /path/to/your-key ubuntu@PUBLIC_IP
cd ~/ai-trading-bot
sudo bash deploy/setup.sh
# put the GLM key into the env file (or rely on config.json already copied)
nano deploy/env        # paste your key, Ctrl+O, Ctrl+X
sudo systemctl restart aitb-race-llm aitb-pol
```

Done. All six services now run 24/7, auto-restart on crash, and start on
reboot. Optional, for signed testnet orders later:
`deploy/venv/bin/pip install hyperliquid-python-sdk`.

## 4. Dashboard (safe, through an SSH tunnel)

From the laptop:

```bash
ssh -i /path/to/your-key -N -L 8787:127.0.0.1:8787 ubuntu@PUBLIC_IP
```

Keep that running and open `http://127.0.0.1:8787` as before. Do NOT open
port 8787 in the Oracle firewall — the dashboard has no login by design.

## 5. Day-to-day on the server

```bash
systemctl status aitb-race-llm            # is a book alive?
journalctl -u aitb-race-llm -f            # live logs of one book
journalctl -u aitb-pol --since "1 hour ago"
sudo systemctl restart aitb-collector     # bounce a service
sudo systemctl stop aitb-race-sma         # retire a book
python3 -m bot.pol.report                 # calibration meter (from repo root)
```

## 6. Handoff checklist (important)

1. **Stop the laptop bots BEFORE starting server services** — two copies of
   the same book would trade independently and split their history. Close all
   bot windows (or `taskkill /IM python.exe` carefully) after the scp in §2.
2. The state files (`output/*.json`, `*.db`) travel with the copy — books
   resume exactly where they left off, no re-trading of old bars.
3. The laptop can then sleep all it wants. When you eventually move to a
   paid VPS, the same steps apply (scp + setup.sh).

## Updating the code on the server later

```bash
# from laptop: re-copy changed files
scp -i key -r ai-trading-bot ubuntu@IP:~/
# on server:
sudo systemctl restart aitb-race-llm aitb-race-carry ...
```
