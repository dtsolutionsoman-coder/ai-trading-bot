#!/usr/bin/env bash
# ============================================================
#  AI Trading Bot — one-shot server deployment (Ubuntu)
#
#  Usage on the server:      sudo bash deploy/setup.sh
#  Preview without changes:  bash deploy/setup.sh --dry-run
#
#  Creates a venv, installs deps, writes systemd units for every
#  bot, enables and starts them. Re-runnable: it refreshes units.
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN="${1:-}"
SVC_USER="${SUDO_USER:-$(id -un)}"
VENV="$ROOT/deploy/venv"
UNIT_DIR=/etc/systemd/system

# service name | module + args
SERVICES=(
  "aitb-dashboard|bot.dashboard"
  "aitb-collector|bot.data.run --coins BTC,ETH,SOL --loop"
  "aitb-race-sma|bot.live.run --strategy sma_cross --symbol BTC --interval 15m --state output/race_sma.json"
  "aitb-race-llm|bot.live.run --strategy llm_analyst --symbol BTC --interval 15m --every 4 --context-db output/market_data.db --state output/live_llm.json"
  "aitb-race-carry|bot.live.run --strategy funding_carry --symbol BTC --interval 15m --network mainnet --context-db output/market_data_mainnet.db --state output/live_carry.json"
  "aitb-pol|bot.pol.run --loop"
)

step() { echo "==> $*"; }

# ---- 1. system packages --------------------------------------------------
if [ -z "$DRY_RUN" ]; then
  step "installing python3-venv (apt)"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv >/dev/null
else
  step "DRY-RUN: would apt-get install python3-venv"
fi

# ---- 2. virtualenv --------------------------------------------------------
if [ -z "$DRY_RUN" ]; then
  step "creating venv at $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$ROOT/requirements.txt"
else
  step "DRY-RUN: would create venv + pip install -r requirements.txt"
fi

# ---- 3. env file ----------------------------------------------------------
if [ ! -f "$ROOT/deploy/env" ]; then
  if [ -n "$DRY_RUN" ]; then
    step "DRY-RUN: would create deploy/env from env.example"
  else
    step "creating deploy/env from env.example (EDIT IT with your key)"
    cp "$ROOT/deploy/env.example" "$ROOT/deploy/env"
  fi
fi

# ---- 4. systemd units -----------------------------------------------------
for entry in "${SERVICES[@]}"; do
  name="${entry%%|*}"
  cmd="${entry#*|}"
  unit="$UNIT_DIR/$name.service"
  body="[Unit]
Description=AI Trading Bot — $name
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/deploy/env
ExecStart=$VENV/bin/python -m $cmd
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"
  if [ -z "$DRY_RUN" ]; then
    step "writing $unit"
    printf '%s\n' "$body" > "$unit"
  else
    step "DRY-RUN: would write $unit for: python -m $cmd"
  fi
done

# ---- 5. enable + start ----------------------------------------------------
if [ -z "$DRY_RUN" ]; then
  step "enabling + starting services"
  systemctl daemon-reload
  for entry in "${SERVICES[@]}"; do
    systemctl enable --now "${entry%%|*}" >/dev/null 2>&1
  done
  echo
  echo "all services started. useful commands:"
  echo "  systemctl status aitb-race-llm"
  echo "  journalctl -u aitb-pol -f          # live logs"
  echo "  sudo systemctl restart aitb-collector"
  echo "  sudo systemctl stop aitb-race-sma  # stop one book"
else
  step "DRY-RUN: would systemctl daemon-reload + enable --now all services"
fi
