@echo off
rem ============================================================
rem  AI Trading Bot - start everything on this PC
rem  Double-click this file. Four windows open, browser follows.
rem  Stop any bot by closing its window (state auto-saves).
rem ============================================================
set PY=C:\Users\Graph\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
cd /d "%~dp0"

start "Dashboard" cmd /k "%PY% -m bot.dashboard"

start "Live Bot - Hyperliquid paper (SMA, no keys)" cmd /k "%PY% -m bot.live.run --strategy sma_cross --symbol BTC --interval 1h"

start "Data Collector - funding/OI history" cmd /k "%PY% -m bot.data.run --coins BTC,ETH,SOL --loop"

start "Polymarket - exits only until LLM key set" cmd /k "%PY% -m bot.pol.run --loop"

ping -n 4 127.0.0.1 >nul
start http://127.0.0.1:8787
echo All bots started; browser opening. This window will close itself.

