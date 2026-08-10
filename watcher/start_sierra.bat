@echo off
REM Sierra Engine against Sierra Pacific's PRODUCTION workspace.
REM Reads watcher\.env.sierra and only acts in the channels listed there.
REM For the demo workspace use start_engine.bat instead.
cd /d "%~dp0"
echo Starting Sierra Engine on the SIERRA PACIFIC workspace...
echo Check the "listening" line below says workspace=Sierra Pacific before testing.
python slack_engine.py --env .env.sierra
pause
