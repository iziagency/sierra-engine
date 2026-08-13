# Running the Sierra Engine 24/7 on your own machine

This moves the engine off the contractor's workstation and onto a Sierra
Pacific PC that stays powered on, signed in to **your** Claude account. After
this, the engine answers in the Slack channel on its own — nobody has to start
anything in the morning or leave a terminal window open.

Nothing about how it behaves changes. Same channel, same commands, same
outputs, same Drive folders. Only the machine underneath it changes.

## What actually runs

```
Slack channel  ──►  Sierra Engine (Python, Socket Mode)  ──►  Google Drive
   #ai-testings          on your 24/7 PC                       "Claude" drive
                              │
                              └──►  claude -p  (your Claude plan)
```

Socket Mode means the engine dials out to Slack. There is **no inbound port, no
firewall change, and nothing published to the internet** — the PC is a client,
not a server. That is also why this works from a normal office network.

The engine calls the Claude Code CLI locally for every extraction. It runs
under the Windows user you install it as, so the usage lands on that user's
Claude plan. Install it as the account you want billed.

---

## Before you start

Six things. The installer refuses to continue until all six are true, so you
can run it early just to see what is still missing.

| # | What | How to get it |
|---|------|---------------|
| 1 | **Windows PC that stays on** | Any machine that is not shut down at night. The screen may sleep and lock; the machine may not. The installer turns sleep off for you. |
| 2 | **Python 3.12 or newer** | [python.org](https://www.python.org/downloads/windows/) — tick **"Add python.exe to PATH"** during setup. |
| 3 | **The engine files** | `git clone https://github.com/iziagency/sierra-engine.git C:\sierra-pacific`, or download the ZIP from that page. Public repo — no account, no invitation. See *Getting the code* below. Any path works; the commands here assume `C:\sierra-pacific`. |
| 4 | **Claude Code, signed in as you** | Install [Node.js LTS](https://nodejs.org/), then `npm i -g @anthropic-ai/claude-code`. Open a terminal **as the Windows user that will host the engine**, run `claude`, then `/login`. This is the step that decides whose plan pays. |
| 5 | **`watcher\.env.sierra`** | The two Slack tokens and the channel allowlist. You create this file yourself, on this machine, from values you read out of Slack. See *The Slack tokens* below. |
| 6 | **A Google Drive credential** | See *The Drive credential* below. Read it before you install — it is the one thing that will quietly break a week later if you skip it. |

### Why you create the credentials instead of being handed them

Every secret this engine needs is generated on this machine, by you, and never
travels. That is not ceremony. A token sent over email, Slack or WhatsApp has
been read by every system it passed through and cannot be un-sent, and the two
Slack tokens are enough for anyone holding them to post as the bot into a
channel where real client work gets filed.

It also fixes something worth fixing on its own: after this, Sierra Pacific owns
every credential the engine runs on. Nothing depends on a contractor's account
still existing.

## Getting the code

    https://github.com/iziagency/sierra-engine

No account, no invitation, no sign-up. Either way works:

**With git** — recommended, because updating later is one command:

```powershell
git clone https://github.com/iziagency/sierra-engine.git C:\sierra-pacific
```

Needs [Git for Windows](https://git-scm.com/download/win) first.

**Without git** — open the page, green *Code* button, *Download ZIP*. Extract it
to `C:\` and rename the folder from `sierra-engine-main` to `sierra-pacific`, so
every command in this guide matches without editing. Then confirm
`C:\sierra-pacific\deploy\windows\install.ps1` exists; if it does not, the ZIP
was extracted one level too deep.

### Updates

**If you cloned with git, updates arrive on their own.** The installer registers
a second task, `SierraEngineUpdate`, that checks the repo every 30 minutes and,
only when something actually changed, pulls it and restarts the engine. A fix
pushed to the repo reaches this machine within half an hour with nobody touching
it. Because the repo is public, the pull needs no login and no key.

That restart is the only time the engine blinks, and it happens only on a real
change, so a quiet week is a quiet engine. What it did, and when, is in
`watcher\update.out.log`.

To pull a fix immediately instead of waiting for the next check:

```powershell
Start-ScheduledTask -TaskName SierraEngineUpdate
```

Or by hand:

```powershell
cd C:\sierra-pacific; git pull; Restart-ScheduledTask -TaskName SierraEngine
```

**If you installed from the ZIP, none of that applies** — a ZIP has no repository
to pull from, and the installer says so plainly instead of pretending it will
update. Download the ZIP again and extract over the top, then restart the task.
This is the reason to prefer `git clone`: it is the difference between fixes that
land by themselves and fixes that need a person at this machine.

### What is not in the repo, and why

The engine, and nothing else. No playbooks, no meeting notes, no client file —
the engine reads all of that from Slack and from Drive while it runs, so shipping
copies would only create a second, staler set. The client names in the comments
and tests are invented; the rules they document are real.

No credential is in here either. The Slack tokens, the Drive service-account key
and the model session are all created on this machine, by you. That is the point:
they never travel, and nothing depends on a contractor's account.

## The Slack tokens

The engine reads these from `watcher\.env.sierra`, which is deliberately never
committed. Create it here with the values you read out of Slack:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and open the **Sierra
   Engine** app. If it is not listed, ask Rafael to add you as a collaborator on
   it — that is a setting on the app, and it is worth doing anyway so the app
   does not depend on his account.
2. *OAuth & Permissions* → copy the **Bot User OAuth Token**. It starts `xoxb-`.
3. *Basic Information* → *App-Level Tokens* → open the Socket Mode token and
   copy it. It starts `xapp-`. If there is none, create one with the
   `connections:write` scope.
4. Create `C:\sierra-pacific\watcher\.env.sierra` with exactly three lines:

   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_APP_TOKEN=xapp-...
   SLACK_ALLOWED_CHANNELS=ai-testings
   ```

`SLACK_ALLOWED_CHANNELS` is a comma-separated allowlist. The engine ignores
every channel that is not on it, so start with the testing channel and widen it
deliberately. **Leaving it empty means every channel the bot is in**, which in
the real workspace means being invited to the wrong channel would silently start
filing real client work.

## The Drive credential — settle this before going unattended

The engine writes deliverables to the **Claude** shared drive. Today it does
that with a personal Google OAuth token whose consent screen is still in
*testing* mode, and Google expires those refresh tokens **after 7 days**.

On a staffed desk that is an annoyance — somebody re-authorises. On a machine
nobody is watching, it is an outage that begins on a Sunday, silently fails
every drop, and gets noticed on Monday.

The fix is a **service account**: a robot Google identity with no consent
screen and nothing to expire.

1. In the Google Cloud console, project **sierra-engine** → *IAM & Admin* →
   *Service Accounts* → **Create service account**. Name it `sierra-engine`.
   No roles are needed — Drive access is granted by sharing, not by IAM.
2. On the new account: *Keys* → *Add key* → *Create new key* → **JSON**.
   Save the downloaded file as `watcher\service_account.json` on the PC.
3. Copy the account's address (it ends in `.iam.gserviceaccount.com`).
4. In Google Drive, open the **Claude** shared drive → *Manage members* →
   add that address as **Content manager**.

That is all. The engine picks the key up automatically — if
`watcher\service_account.json` exists it is used, and `token.json` is left
completely alone. Machines without the key keep working exactly as before.

> If the shared drive refuses to add the address, Workspace sharing settings
> are blocking non-domain members. An admin has to allow it for that drive.

## On a Mac

The engine is the same portable Python on a Mac; only the 24/7 wrapper differs.
Everything above still applies — the same six prerequisites, the same
service-account credential, the same `.env.sierra` — with these substitutions:

- **Prerequisites**: `brew install python@3.12 git node`, then
  `npm i -g @anthropic-ai/claude-code`, then `claude` → `/login` as the user that
  will host the engine.
- **The credential file** goes to `watcher/service_account.json` (forward
  slashes, under wherever you cloned the repo, e.g. `~/sierra-pacific`).
- **Install**: no Administrator, no PowerShell. In Terminal, as the host user:

  ```bash
  bash ~/sierra-pacific/deploy/mac/install.sh
  ```

  It runs the same checks, then registers two **launchd** agents:
  `com.sierra.engine` (the engine, restarts on crash, runs at login) and
  `com.sierra.engine.update` (the 30-minute auto-update). It asks once for your
  password to disable sleep. A good run ends with the same green `listening ·
  workspace=Sierra Pacific` line.

- **For true 24/7**, the Mac must log the host user in after a reboot and never
  sleep: *System Settings → Users & Groups → Automatic login* on, and
  *System Settings → Displays/Battery → Prevent automatic sleeping* on power.
- **Day to day** (Terminal): running? `launchctl print gui/$(id -u)/com.sierra.engine | grep state`.
  Logs: `tail -f ~/sierra-pacific/watcher/engine.out.log`. Pull a fix now:
  `launchctl kickstart -k gui/$(id -u)/com.sierra.engine.update`. Stop:
  `launchctl bootout gui/$(id -u)/com.sierra.engine`. Remove everything:
  `bash deploy/mac/install.sh --uninstall`.

The web reports, SAFER and SOS need the browser dependency, same as on Windows:
`python3 -m pip install playwright && python3 -m playwright install chromium`.

The rest of this guide is written for Windows; read `\` as `/` and the scheduled
task as the launchd agent.

## Install (Windows)

Open **PowerShell as Administrator**, signed in as the user from step 4:

```powershell
powershell -ExecutionPolicy Bypass -File C:\sierra-pacific\deploy\windows\install.ps1
```

It checks the six prerequisites, installs the Python packages, disables sleep,
registers a scheduled task called **SierraEngine**, starts it, and then waits
for the engine to announce itself. A good run ends with a green line like:

```
[sierra-engine] listening · workspace=Sierra Pacific (T0…) · env=.env.sierra · channels=…
```

**Read that line before anyone uses it.** It has to say
`workspace=Sierra Pacific`. If it names a different workspace, the engine is
holding the demo tokens and would write into the wrong place.

## Prove it survives a restart

The whole point is that nobody has to be there. So test the part that has to
work when nobody is:

1. Reboot the PC. Do not sign in.
2. From another machine, post a command in the allowed channel.
3. You should get the normal reply. If you do not, sign in and check
   `Get-Content C:\sierra-pacific\watcher\engine.out.log -Tail 40`.

If the installer reported `logon type: Interactive` instead of `S4U`, the task
only runs while you are signed in, and step 2 will fail on purpose. Turn on
automatic sign-in — `netplwiz`, untick *"Users must enter a user name and
password"* — and repeat the test.

## Day to day

| Need | Command (PowerShell) |
|---|---|
| Is it running? | `Get-ScheduledTask SierraEngine \| Get-ScheduledTaskInfo` |
| What has it been doing? | `Get-Content C:\sierra-pacific\watcher\engine.out.log -Tail 40` |
| Watch it live | `Get-Content …\engine.out.log -Tail 20 -Wait` |
| Stop it | `Stop-ScheduledTask -TaskName SierraEngine` |
| Start it | `Start-ScheduledTask -TaskName SierraEngine` |
| Pull a fix now | `Start-ScheduledTask -TaskName SierraEngineUpdate` |
| What updates have run | `Get-Content C:\sierra-pacific\watcher\update.out.log -Tail 20` |
| Remove it entirely | `.\install.ps1 -Uninstall` |

The engine restarts itself if it crashes or loses its connection — the
supervisor loop waits 15 seconds and brings it back, and the log records every
restart with a timestamp. A short burst of restart lines is normal after a
network blip. A restart every 15 seconds for an hour is not; read the lines in
between, the reason is printed there.

## The three things that will take it down

**Two engines at once.** Two Socket Mode connections on one Slack app make
Slack deliver each event to exactly one of them, so roughly half the drops
disappear with no error anywhere. Never run `python slack_engine.py` by hand
while the scheduled task is running. The `engine.pid` lock refuses the second
copy, which is why it exists — do not delete it to "fix" a startup problem.

**The PC sleeping.** A sleeping machine drops the connection and answers
nothing until someone wakes it. The installer disables sleep and hibernate on
AC power; if it warned that it could not, set *Settings → System → Power →
Sleep* to **Never** by hand.

**A credential quietly expiring.** The Drive service account above closes the
7-day hole. The Claude Code login refreshes itself and needs no attention. The
Slack tokens do not expire, but the Slack app is still configured under a
contractor's account. Adding yourself as a collaborator on it (step 1 of *The
Slack tokens*) is the minimum; transferring ownership to a Sierra Pacific
account is what makes it survive personnel changes.

If you later add a deploy key for one-command updates, the same applies to it:
it does not expire and it is read-only on one repository, but it is still a key
sitting on a PC. If that machine is ever retired or changes hands, tell Rafael so
it can be revoked.

## What the engine will not do, by design

- It only answers in the channels listed in `.env.sierra`. Anything posted
  anywhere else is ignored.
- The production drives — **Prospects**, **Clients**, **Docs** — are read-only
  in code. A write outside the Claude drive raises an error rather than
  succeeding. This guard is not configurable at runtime on purpose.
- A confirmation older than 24 hours no longer counts as a `yes`.
