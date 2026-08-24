@echo off
rem ============================================================
rem  AI bot launcher - LLM brain + Polymarket with AI entries.
rem  Requires your API key saved in config.json first.
rem  NOTE: close the old "Polymarket" window before running
rem  this (it was started without the key).
rem ============================================================
set PY=C:\Users\Graph\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
cd /d "%~dp0"

start "Live Bot - GLM AI (paper, 15m)" cmd /k "%PY% -m bot.live.run --strategy llm_analyst --symbol BTC --interval 15m --every 4 --context-db output/market_data.db --state output/live_llm.json"

start "Polymarket - AI entries ON" cmd /k "%PY% -m bot.pol.run --loop"

ping -n 3 127.0.0.1 >nul
start http://127.0.0.1:8787
echo AI bots started; browser opening. This window will close itself.
