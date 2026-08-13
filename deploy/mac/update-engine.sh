#!/bin/bash
#
# Sierra Engine auto-update (Mac). The launchd counterpart of
# deploy/windows/update-engine.ps1.
#
# Runs every 30 minutes from the com.sierra.engine.update agent so a fix pushed
# to the public repo lands here without anyone touching the machine. Pulls, and
# restarts the engine ONLY when the pull actually changed something - a restart
# drops the Slack connection for a second, so it is not done on a quiet check.

set -uo pipefail

LABEL="com.sierra.engine"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WATCHER="$REPO/watcher"
LOG="$WATCHER/update.out.log"

note() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG"; }

# A tarball/ZIP install has no repository to pull from. Say so once and stop -
# not an error, just a machine that opted out of auto-update by how it was set up.
if [ ! -d "$REPO/.git" ]; then
    note "not a git checkout - auto-update skipped. Reinstall via git clone to enable."
    exit 0
fi

cd "$REPO" || { note "cannot cd to repo"; exit 1; }

GIT="$(command -v git || true)"
[ -n "$GIT" ] || { note "git not found on PATH - cannot auto-update"; exit 1; }

before="$("$GIT" rev-parse HEAD 2>/dev/null)"
"$GIT" fetch --quiet origin main 2>/dev/null || { note "could not reach origin - will retry next run"; exit 1; }
remote="$("$GIT" rev-parse origin/main 2>/dev/null)"

[ -n "$remote" ] || { note "no origin/main - skipping"; exit 1; }
[ "$before" = "$remote" ] && exit 0   # nothing new; leave the engine alone

note "update available: ${before:0:7} -> ${remote:0:7}"
# --ff-only: never create a merge commit on a client's machine. If the local
# tree diverged, refuse and keep running the old code rather than guess.
out="$("$GIT" pull --ff-only origin main 2>&1)"
note "$out"
after="$("$GIT" rev-parse HEAD 2>/dev/null)"
if [ "$after" = "$before" ]; then
    note "pull did not advance HEAD - engine left running on old code"
    exit 1
fi

note "restarting engine at ${after:0:7}"
launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
note "restarted on the new code"
