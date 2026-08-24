@echo off
rem Polymarket bot launcher (fixed pacing) — used after rate-limit fix.
set PY=C:\Users\Graph\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
cd /d "%~dp0"
start "Polymarket - AI entries ON" cmd /k "%PY% -m bot.pol.run --loop"
