#!/bin/bash
#
# Sierra Engine - unattended install on a Mac that stays on 24/7.
#
# Run from anywhere:
#     bash /path/to/sierra-pacific/deploy/mac/install.sh
#
# It checks every prerequisite first and stops on the first one missing, because
# a half-installed engine that answers some drops and silently ignores others is
# the single worst outcome here. Nothing is installed until all checks pass.
#
# Remove it again with:  bash install.sh --uninstall
#
# The Mac counterpart of deploy/windows/install.ps1. Same engine, same behaviour;
# only the machine underneath changes. On a Mac the 24/7 service is a launchd
# LaunchAgent (runs under the logged-in user, so `claude -p` reads THIS user's
# credential and the usage lands on their plan), and auto-update is a second
# agent on a 30-minute timer.

set -euo pipefail

LABEL="com.sierra.engine"
UPDATE_LABEL="com.sierra.engine.update"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WATCHER="$REPO/watcher"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
UPDATE_PLIST="$AGENTS/$UPDATE_LABEL.plist"
LOG="$WATCHER/engine.out.log"

say()  { printf '  %s\n' "$1"; }
ok()   { printf '  \033[32m[ok]\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33m[warn]\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m[stop]\033[0m %s\n' "$1"; exit 1; }

# --- uninstall -------------------------------------------------------------
if [ "${1:-}" = "--uninstall" ]; then
    echo; echo "Removing the Sierra Engine service"; echo
    for l in "$UPDATE_LABEL" "$LABEL"; do
        launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true
    done
    rm -f "$PLIST" "$UPDATE_PLIST" "$WATCHER/engine.pid"
    ok "launchd agents removed. Power settings were left as they are."
    exit 0
fi

echo; echo "Sierra Engine - install (Mac)"; echo
say "repo: $REPO"

# --- 1. Python 3.12+ -------------------------------------------------------
# Resolved once, here, and written into the launchd plist. launchd hands the
# agent a thin PATH, so trusting `python3` to be findable later is the exact
# assumption that breaks unattended.
PYTHON=""
for cand in python3.13 python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        v="$("$cand" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)"
        if [ "$v" -ge 312 ]; then PYTHON="$(command -v "$cand")"; break; fi
    fi
done
[ -n "$PYTHON" ] || die "Python 3.12+ not found. Install it (brew install python@3.12), then run this again."
ok "python: $PYTHON"

# --- 2. Claude Code, signed in ---------------------------------------------
# The engine shells out to `claude -p` for every extraction. The login has to
# belong to the account whose plan absorbs the usage, because the credential is
# read from THIS user's ~/.claude.
CLAUDE="$(command -v claude || true)"
[ -n "$CLAUDE" ] || die "Claude Code CLI not found. Install Node.js LTS, then: npm i -g @anthropic-ai/claude-code"
[ -f "$HOME/.claude/.credentials.json" ] || die "Claude Code is installed but not signed in for user '$USER'. In a terminal AS THIS USER run 'claude', then '/login'."
ok "claude: $CLAUDE (signed in as '$USER')"

# --- 3. Slack tokens -------------------------------------------------------
ENVFILE="$WATCHER/.env.sierra"
[ -f "$ENVFILE" ] || die "Missing watcher/.env.sierra - copy it across by hand. It carries the Slack tokens and the channel allowlist and is never committed."
for k in SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_ALLOWED_CHANNELS; do
    grep -Eq "^[[:space:]]*$k[[:space:]]*=[[:space:]]*[^[:space:]]" "$ENVFILE" || die "watcher/.env.sierra has no value for $k."
done
ok "slack: .env.sierra has both tokens and a channel allowlist"

# --- 4. Drive credential ---------------------------------------------------
# The personal OAuth token's consent screen is still in testing mode, so its
# refresh token dies every 7 days. On a machine nobody watches, that is an
# outage that starts on a Sunday. The service-account key has nothing to expire.
if [ -f "$WATCHER/service_account.json" ]; then
    ok "drive: service account key present (no expiry)"
elif [ -f "$WATCHER/token.json" ]; then
    warn "drive: running on the personal OAuth token. It WILL stop working within 7 days and every drop will fail until re-authorised. Install watcher/service_account.json before leaving this unattended."
else
    die "No Drive credential. Put watcher/service_account.json in place (preferred), or authorise once with: $PYTHON watcher/authorize_url.py"
fi
[ -f "$WATCHER/drive_config.txt" ] || die "Missing watcher/drive_config.txt - it holds the resolved id of the Claude shared drive."

# --- 5. Dependencies -------------------------------------------------------
say "installing python packages..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -r "$REPO/deploy/requirements.txt"
"$PYTHON" -c "import slack_bolt, fitz, openpyxl, reportlab, PIL, googleapiclient" 2>/dev/null || die "packages installed but the imports still fail."
ok "python packages installed and importable"

# --- 6. The machine must not sleep -----------------------------------------
# A sleeping Mac drops the Socket Mode connection. The display may sleep; the
# machine may not. pmset needs sudo, so this is best-effort and reported.
if sudo -n pmset -a sleep 0 disksleep 0 2>/dev/null; then
    ok "power: system sleep and disk sleep disabled"
else
    warn "could not change power settings without a sudo prompt. Run: sudo pmset -a sleep 0 disksleep 0   (or set System Settings > Displays > 'Prevent automatic sleeping' on power adapter)"
fi

mkdir -p "$AGENTS"

# --- Write the engine agent ------------------------------------------------
# claude must be on the agent's PATH for claude_bin()'s `which claude` to find
# it, so its directory is baked in alongside the usual Homebrew locations.
CLAUDE_DIR="$(dirname "$CLAUDE")"
AGENT_PATH="$CLAUDE_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$WATCHER/slack_engine.py</string>
    <string>--env</string>
    <string>.env.sierra</string>
  </array>
  <key>WorkingDirectory</key><string>$WATCHER</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PYTHONIOENCODING</key><string>utf-8</string>
    <key>PATH</key><string>$AGENT_PATH</string>
  </dict>
</dict>
</plist>
PLIST
ok "engine agent written: $PLIST"

# --- Write the auto-update agent -------------------------------------------
cat > "$UPDATE_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$UPDATE_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$HERE/update-engine.sh</string>
  </array>
  <key>StartInterval</key><integer>1800</integer>
  <key>StandardOutPath</key><string>$WATCHER/update.out.log</string>
  <key>StandardErrorPath</key><string>$WATCHER/update.out.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
PLIST
ok "auto-update agent written (every 30 min from the public repo)"

# --- Load them -------------------------------------------------------------
if [ -f "$LOG" ]; then mv "$LOG" "$WATCHER/engine.out.$(date +%Y%m%d-%H%M%S).log" 2>/dev/null || true; fi
rm -f "$WATCHER/engine.pid"
for pair in "$LABEL:$PLIST" "$UPDATE_LABEL:$UPDATE_PLIST"; do
    l="${pair%%:*}"; p="${pair##*:}"
    launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$p"
done
launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
say "started - waiting for it to announce which workspace it is bound to..."

listening=""
for _ in $(seq 1 40); do
    sleep 2
    if [ -f "$LOG" ] && grep -q 'listening .*workspace=' "$LOG" 2>/dev/null; then
        listening="$(grep 'listening .*workspace=' "$LOG" | tail -1)"
        break
    fi
done

echo
if [ -n "$listening" ]; then
    ok "the engine is live:"
    printf '         \033[32m%s\033[0m\n\n' "$listening"
    say "Confirm that line says workspace=Sierra Pacific before anyone uses it,"
    say "and drive=... service account (no expiry). Then post a real command in"
    say "the allowed channel and watch for the reply."
else
    warn "no 'listening' line after 80 seconds. Read the log: tail -40 '$LOG'"
fi
echo
echo "  logs:   tail -f '$LOG'"
echo "  stop:   launchctl bootout gui/$(id -u)/$LABEL"
echo "  start:  launchctl bootstrap gui/$(id -u) '$PLIST'"
echo "  remove: bash '$HERE/install.sh' --uninstall"
echo
