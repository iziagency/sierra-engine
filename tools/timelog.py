"""Simple work-hours logger for the Sierra Pacific engagement.

Usage:
    python timelog.py start "Lakeside QP test"            # start the clock
    python timelog.py start "RTS excel filler" --shots   # + screenshot every 5 min
    python timelog.py stop                               # stop and save
    python timelog.py status                             # current session + week total
    python timelog.py add "task description" 2026-07-21 3.5   # backfill hours
    python timelog.py report                             # weekly report (paste to JC)

Data lives next to this script: timelog.csv, session.json, shots/<date>/.
Budget: 10 h/week, week resets Monday (per JC, call 2026-07-22).
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "timelog.csv"
SESSION = HERE / "session.json"
SHOTS_DIR = HERE / "shots"
BUDGET_HOURS = 10.0
SHOT_INTERVAL_MIN = 5.0


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    with open(LOG, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_log(row: dict) -> None:
    exists = LOG.exists()
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "start", "end", "hours", "task"])
        if not exists:
            w.writeheader()
        w.writerow(row)


def week_bounds(day: datetime) -> tuple[datetime, datetime]:
    monday = day - timedelta(days=day.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return monday, monday + timedelta(days=7)


def week_hours(rows: list[dict], day: datetime) -> float:
    lo, hi = week_bounds(day)
    total = 0.0
    for r in rows:
        d = datetime.strptime(r["date"], "%Y-%m-%d")
        if lo <= d < hi:
            total += float(r["hours"])
    return total


def cmd_start(task: str, shots: bool, interval_min: float) -> None:
    if SESSION.exists():
        print("A session is already running - `stop` it first.")
        sys.exit(1)
    state = {"task": task, "start": datetime.now().isoformat(timespec="seconds"),
             "shots": shots}
    SESSION.write_text(json.dumps(state), encoding="utf-8")
    print(f"Started: {task}  ({state['start']})")
    if shots:
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_shotloop", str(interval_min)],
            creationflags=flags, close_fds=True,
        )
        print(f"Screenshots every {interval_min:g} min -> {SHOTS_DIR}")


def cmd_stop() -> None:
    if not SESSION.exists():
        print("No session running.")
        sys.exit(1)
    state = json.loads(SESSION.read_text(encoding="utf-8"))
    SESSION.unlink()  # also signals the screenshot loop to exit
    start = datetime.fromisoformat(state["start"])
    end = datetime.now()
    hours = round((end - start).total_seconds() / 3600, 2)
    append_log({
        "date": start.strftime("%Y-%m-%d"),
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "hours": f"{hours}",
        "task": state["task"],
    })
    total = week_hours(read_log(), start)
    print(f"Stopped: {state['task']} - {hours} h logged.")
    print(f"Week total: {total:g} / {BUDGET_HOURS:g} h")


def cmd_status() -> None:
    rows = read_log()
    now = datetime.now()
    if SESSION.exists():
        state = json.loads(SESSION.read_text(encoding="utf-8"))
        start = datetime.fromisoformat(state["start"])
        running = (now - start).total_seconds() / 3600
        print(f"RUNNING: {state['task']} - {running:.2f} h so far (started {state['start']})")
    else:
        print("No session running.")
    print(f"Week total (logged): {week_hours(rows, now):g} / {BUDGET_HOURS:g} h")


def cmd_add(task: str, date_str: str, hours: float) -> None:
    append_log({"date": date_str, "start": "", "end": "", "hours": f"{hours}", "task": task})
    print(f"Backfilled {hours:g} h on {date_str}: {task}")


def cmd_report() -> None:
    rows = read_log()
    if not rows:
        print("No entries yet.")
        return
    now = datetime.now()
    lo, hi = week_bounds(now)
    week = [r for r in rows
            if lo <= datetime.strptime(r["date"], "%Y-%m-%d") < hi]
    total = sum(float(r["hours"]) for r in week)
    print(f"Hours report - week of {lo.strftime('%b %d')} to {(hi - timedelta(days=1)).strftime('%b %d, %Y')}")
    print()
    by_day: dict[str, list[dict]] = {}
    for r in week:
        by_day.setdefault(r["date"], []).append(r)
    for date in sorted(by_day):
        day_rows = by_day[date]
        day_total = sum(float(r["hours"]) for r in day_rows)
        label = datetime.strptime(date, "%Y-%m-%d").strftime("%a %b %d")
        print(f"{label} - {day_total:g} h")
        for r in day_rows:
            span = f" ({r['start']}-{r['end']})" if r["start"] else ""
            print(f"  - {r['task']}{span}: {r['hours']} h")
    print()
    print(f"TOTAL: {total:g} h of {BUDGET_HOURS:g} h budget")


def cmd_shotloop(interval_min: float) -> None:
    """Hidden: runs detached, captures screen until session.json disappears."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return
    while SESSION.exists():
        day_dir = SHOTS_DIR / datetime.now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        img = ImageGrab.grab()
        img = img.resize((img.width // 2, img.height // 2))
        img.convert("RGB").save(day_dir / f"{datetime.now().strftime('%H%M%S')}.jpg",
                                quality=55)
        for _ in range(int(interval_min * 60)):
            if not SESSION.exists():
                return
            time.sleep(1)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    if cmd == "start":
        task = args[1] if len(args) > 1 else "work"
        shots = "--shots" in args
        interval = SHOT_INTERVAL_MIN
        if "--interval" in args:
            interval = float(args[args.index("--interval") + 1])
        cmd_start(task, shots, interval)
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "add":
        cmd_add(args[1], args[2], float(args[3]))
    elif cmd == "report":
        cmd_report()
    elif cmd == "_shotloop":
        cmd_shotloop(float(args[1]))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
