@echo off
REM Sierra Engine - Slack listener launcher (hub machine)
REM Double-click to start, or run from a terminal. Ctrl+C to stop.
cd /d "%~dp0"
echo Starting Sierra Engine (Slack -^> cap-app -^> Drive)...
echo Drop files in #app-intake. Ctrl+C to stop.
python slack_engine.py
pause
