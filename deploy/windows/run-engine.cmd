@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM Sierra Engine supervisor - the thing Task Scheduler actually launches.
REM
REM Task Scheduler's own "restart on failure" only fires when a task FAILS, and
REM a Python process that exits 0 is not a failure. So the restart lives here,
REM in a loop, and covers every way the engine can stop: a crash, a dropped
REM Socket Mode connection, a killed process, anything.
REM
REM Never run this at the same time as a manual `python slack_engine.py`. Two
REM Socket Mode connections on one Slack app make Slack hand each event to
REM exactly one of them, and half the drops vanish with no error anywhere.
REM The engine.pid lock refuses the second copy, which is why that lock exists.
REM ===========================================================================

REM UTF-8 or the first startup line kills the engine. The engine prints
REM "listening - workspace=..." with a middle dot, and a non-interactive console
REM defaults to cp1252, where that single character raises UnicodeEncodeError
REM before Slack is ever contacted. It looks exactly like a crash on boot.
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
chcp 65001 > nul 2>&1

cd /d "%~dp0..\..\watcher"

REM The interpreter is resolved once at install time and written down, not
REM discovered at boot. Task Scheduler hands the task a much thinner PATH than
REM an interactive shell, so "it works when I double-click it" proves nothing
REM about what happens after a 3 a.m. reboot.
set PY=
if exist "%~dp0python-path.txt" set /p PY=<"%~dp0python-path.txt"
if not defined PY set PY=python

set LOG=%CD%\engine.out.log

REM Months of restart lines add up. Keep one previous generation, drop the rest.
if exist "%LOG%" (
    for %%A in ("%LOG%") do if %%~zA GTR 20971520 (
        move /y "%LOG%" "%CD%\engine.out.1.log" > nul 2>&1
    )
)

:loop
echo. >> "%LOG%"
echo [supervisor] starting engine at %date% %time% >> "%LOG%"
"%PY%" slack_engine.py --env .env.sierra >> "%LOG%" 2>&1
echo [supervisor] engine exited with code !errorlevel! at %date% %time% - restarting in 15s >> "%LOG%"
timeout /t 15 /nobreak > nul
goto loop
