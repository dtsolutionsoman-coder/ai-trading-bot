@echo off
rem ============================================================
rem  THE HORSE RACE - three strategies, same market, own books.
rem  1. SMA cross      (dumb baseline - must be beaten)
rem  2. GLM-5.3 AI     (position-aware, funding/OI enriched)
rem  3. Funding carry  (harvests extreme funding - no prediction)
rem  Watch at http://127.0.0.1:8787
rem ============================================================
set PY=C:\Users\Graph\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
cd /d "%~dp0"

start "Race - SMA control 15m" cmd /k "%PY% -m bot.live.run --strategy sma_cross --symbol BTC --interval 15m --state output/race_sma.json"

start "Race - GLM AI 15m" cmd /k "%PY% -m bot.live.run --strategy llm_analyst --symbol BTC --interval 15m --every 4 --context-db output/market_data.db --state output/live_llm.json"

start "Race - Funding Carry 15m (mainnet data)" cmd /k "%PY% -m bot.live.run --strategy funding_carry --symbol BTC --interval 15m --network mainnet --context-db output/market_data_mainnet.db --state output/live_carry.json"

ping -n 3 127.0.0.1 >nul
start http://127.0.0.1:8787
echo Race started; browser opening. This window will close itself.
