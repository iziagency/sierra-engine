<#
    Sierra Engine - unattended install on the machine that stays on 24/7.

    Run from an elevated PowerShell, from anywhere:

        powershell -ExecutionPolicy Bypass -File C:\sierra-pacific\deploy\windows\install.ps1

    It checks every prerequisite first and stops on the first one that is
    missing, because a half-installed engine that answers some drops and
    silently ignores others is the single worst outcome here. Nothing is
    installed until all six checks pass.

    Remove it again with:  .\install.ps1 -Uninstall

    ASCII ONLY, deliberately. Windows PowerShell 5.1 reads a .ps1 with no BOM
    as Windows-1252, and an em dash decodes there into three bytes ending in a
    curly closing quote - which the parser accepts as the end of a string. The
    script then fails to parse for a reason nothing in the error message
    explains. Keep every character in this file plain ASCII.
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'SierraEngine',
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'
$Repo    = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Watcher = Join-Path $Repo 'watcher'
$Runner  = Join-Path $PSScriptRoot 'run-engine.cmd'
$Log     = Join-Path $Watcher 'engine.out.log'

function Say  ($m) { Write-Host "  $m" }
function Ok   ($m) { Write-Host "  [ok]   $m"  -ForegroundColor Green }
function Warn ($m) { Write-Host "  [warn] $m"  -ForegroundColor Yellow }
function Die  ($m) { Write-Host "  [stop] $m"  -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
if ($Uninstall) {
    Write-Host "`nRemoving the Sierra Engine service`n"
    if (Get-ScheduledTask -TaskName "${TaskName}Update" -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask       -TaskName "${TaskName}Update" -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName "${TaskName}Update" -Confirm:$false
        Ok "scheduled task '${TaskName}Update' removed"
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask       -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Ok "scheduled task '$TaskName' removed"
    } else {
        Say "no scheduled task '$TaskName' was registered"
    }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*slack_engine*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item (Join-Path $Watcher 'engine.pid') -ErrorAction SilentlyContinue
    Ok "done. Power settings were left as they are."
    exit 0
}

Write-Host "`nSierra Engine - install`n"
Say "repo: $Repo"

# --- 1. Python -------------------------------------------------------------
# Resolved once, here, and written down. Task Scheduler hands the task a much
# thinner PATH than an interactive shell, so trusting 'python' to be findable
# at 3 a.m. is exactly the assumption that breaks unattended.
# Not one quote character in the probe. PowerShell rewrites the quoting on its
# way to a native executable, python receives a mangled -c, exits 1, and the
# whole loop silently concludes that no interpreter exists. Verified 2026-08-07.
$probe  = 'import sys; print(sys.executable); print(sys.version_info[0]); print(sys.version_info[1])'
$python = $null
foreach ($c in @(
    @{ Exe = 'py';     Args = @('-3.12') },
    @{ Exe = 'py';     Args = @('-3')    },
    @{ Exe = 'python'; Args = @()        }
)) {
    try {
        $argv = @($c.Args) + @('-c', $probe)
        $out  = @(& $c.Exe @argv 2>$null)
        if ($LASTEXITCODE -eq 0 -and $out.Count -ge 3 -and
            ([int]$out[1] -gt 3 -or ([int]$out[1] -eq 3 -and [int]$out[2] -ge 12))) {
            $python = $out[0]
            break
        }
    } catch { }
}
if (-not $python) { Die "Python 3.12+ not found. Install it from python.org with 'Add python.exe to PATH' ticked, then run this again." }
Ok "python: $python"

# --- 2. Claude Code, signed in ---------------------------------------------
# The engine shells out to 'claude -p' for every extraction. The login has to
# belong to the account whose plan absorbs the usage - JC's, not a
# contractor's - because the credential is read from THIS user's profile.
$claudeCreds = Join-Path $env:USERPROFILE '.claude\.credentials.json'
$claudeCmd   = Join-Path $env:APPDATA 'npm\claude.cmd'
if (-not (Test-Path $claudeCmd) -and -not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Die "Claude Code CLI not found. Install Node.js LTS, then: npm i -g @anthropic-ai/claude-code"
}
if (-not (Test-Path $claudeCreds)) {
    Die "Claude Code is installed but not signed in for user '$env:USERNAME'. Open a terminal AS THIS USER, run 'claude', then '/login', and sign in with the Anthropic account that should carry the usage."
}
$sub = (Get-Content $claudeCreds -Raw | ConvertFrom-Json).claudeAiOauth.subscriptionType
Ok "claude: signed in as '$env:USERNAME' (plan: $sub)"

# --- 3. Slack tokens -------------------------------------------------------
$envFile = Join-Path $Watcher '.env.sierra'
if (-not (Test-Path $envFile)) { Die "Missing watcher\.env.sierra - copy it across by hand. It carries the Slack tokens and the channel allowlist, and it is deliberately never committed." }
$envText = Get-Content $envFile -Raw
foreach ($k in 'SLACK_BOT_TOKEN','SLACK_APP_TOKEN','SLACK_ALLOWED_CHANNELS') {
    if ($envText -notmatch "(?m)^\s*$k\s*=\s*\S") { Die "watcher\.env.sierra has no value for $k." }
}
Ok "slack: .env.sierra has both tokens and a channel allowlist"

# --- 4. Drive credential ---------------------------------------------------
# The personal OAuth client's consent screen is still in testing mode, so its
# refresh token dies every 7 days. On a staffed desk somebody re-authorises; on
# a machine nobody watches, that is an outage that starts on a Sunday.
$svcAcct = Join-Path $Watcher 'service_account.json'
if (Test-Path $svcAcct) {
    $email = (Get-Content $svcAcct -Raw | ConvertFrom-Json).client_email
    Ok "drive: service account $email (no expiry)"
} elseif (Test-Path (Join-Path $Watcher 'token.json')) {
    Warn "drive: running on the personal OAuth token. It WILL stop working within 7 days and every drop will fail until someone runs 'python watcher\authorize_url.py' again. Install watcher\service_account.json before leaving this machine unattended."
} else {
    Die "No Drive credential. Put watcher\service_account.json in place (preferred), or authorise once with: python watcher\authorize_url.py"
}
if (-not (Test-Path (Join-Path $Watcher 'drive_config.txt'))) { Die "Missing watcher\drive_config.txt - it holds the resolved id of the Claude shared drive." }

# --- 5. Dependencies -------------------------------------------------------
Say "installing python packages..."
& $python -m pip install --quiet --upgrade pip
& $python -m pip install --quiet -r (Join-Path $Repo 'deploy\requirements.txt')
if ($LASTEXITCODE -ne 0) { Die "pip install failed - see the output above." }
& $python -c "import slack_bolt, fitz, openpyxl, reportlab, PIL, googleapiclient" 2>$null
if ($LASTEXITCODE -ne 0) { Die "packages installed but the imports still fail." }
Ok "python packages installed and importable"

Set-Content -Path (Join-Path $PSScriptRoot 'python-path.txt') -Value $python -NoNewline -Encoding ASCII
Ok "interpreter path written for the supervisor"

# --- 6. The machine must not fall asleep -----------------------------------
# A sleeping PC drops the Socket Mode connection. The screen may sleep and the
# session may lock; the machine may not sleep.
try {
    powercfg /change standby-timeout-ac 0   | Out-Null
    powercfg /change hibernate-timeout-ac 0 | Out-Null
    powercfg /change disk-timeout-ac 0      | Out-Null
    powercfg /hibernate off                 | Out-Null
    Ok "power: sleep, hibernate and disk timeout disabled on AC"
} catch {
    Warn "could not change the power plan (needs an elevated PowerShell). Set Settings > System > Power > Sleep to 'Never' by hand, or the engine goes offline whenever the PC does."
}

# --- Register the service --------------------------------------------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Say "replaced the previous '$TaskName' task"
}

$action   = New-ScheduledTaskAction -Execute $Runner -WorkingDirectory $Watcher
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
                -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$user     = "$env:USERDOMAIN\$env:USERNAME"

# S4U first: it survives a reboot with nobody at the keyboard and stores no
# password anywhere. If this account cannot use it, fall back to logon-only,
# which works - but then the PC must sign itself in after a restart or the
# engine simply never comes back and nobody is told.
$mode = 'S4U'
try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings `
        -Trigger (New-ScheduledTaskTrigger -AtStartup) `
        -User $user -RunLevel Limited -LogonType S4U -Force | Out-Null
} catch {
    $mode = 'Interactive'
    Register-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $user) `
        -User $user -RunLevel Limited -LogonType Interactive -Force | Out-Null
}
Ok "scheduled task '$TaskName' registered (logon type: $mode)"
if ($mode -eq 'Interactive') {
    Warn "this task only runs while '$env:USERNAME' is signed in. After a restart the engine stays down until someone logs in. Turn on automatic sign-in (netplwiz, untick 'Users must enter a user name and password') to close that hole."
}

# --- Auto-update -----------------------------------------------------------
# A fix pushed to the public repo should reach this machine without anyone
# touching it. A second task pulls every 30 minutes and restarts the engine only
# when the pull actually changed something. Only works on a git checkout; a ZIP
# install has nothing to pull from, and says so instead of pretending.
$UpdTask = "${TaskName}Update"
if (Test-Path (Join-Path $Repo '.git')) {
    if (Get-ScheduledTask -TaskName $UpdTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $UpdTask -Confirm:$false
    }
    $updRunner  = Join-Path $PSScriptRoot 'update-engine.ps1'
    $updAction  = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NonInteractive -NoProfile -ExecutionPolicy Bypass -File `"$updRunner`" -TaskName $TaskName"
    # Repeat indefinitely: a bare interval without a duration only runs for a day
    # on 5.1, so pin a long finite duration rather than trust the default.
    $updTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $updSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    try {
        Register-ScheduledTask -TaskName $UpdTask -Action $updAction -Trigger $updTrigger `
            -Settings $updSettings -User $user -RunLevel Limited -LogonType S4U -Force | Out-Null
    } catch {
        Register-ScheduledTask -TaskName $UpdTask -Action $updAction -Trigger $updTrigger `
            -Settings $updSettings -User $user -RunLevel Limited -LogonType Interactive -Force | Out-Null
    }
    Ok "auto-update registered ('$UpdTask', every 30 min from the public repo)"
} else {
    if (Get-ScheduledTask -TaskName $UpdTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $UpdTask -Confirm:$false
    }
    Warn "installed from a ZIP, not a git clone - fixes will NOT arrive on their own. To get automatic updates, reinstall after: git clone https://github.com/iziagency/sierra-engine.git"
}

# --- Start it and prove it came up -----------------------------------------
if (Test-Path $Log) {
    Rename-Item $Log "engine.out.$(Get-Date -Format yyyyMMdd-HHmmss).log" -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $Watcher 'engine.pid') -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Say "started - waiting for it to announce which workspace it is bound to..."

$listening = $null
for ($i = 0; $i -lt 40 -and -not $listening; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $Log) {
        $listening = Select-String -Path $Log -Pattern 'listening .*workspace=' -ErrorAction SilentlyContinue |
                     Select-Object -Last 1
    }
}

Write-Host ""
if ($listening) {
    Ok "the engine is live:"
    Write-Host "         $($listening.Line.Trim())" -ForegroundColor Green
    Write-Host ""
    Say "Confirm that line says workspace=Sierra Pacific before anyone uses it."
    Say "Then post a real command in the allowed channel and watch for the reply."
} else {
    Warn "no 'listening' line after 80 seconds. The engine did not come up."
    Say  "Read the log:  Get-Content '$Log' -Tail 40"
}
Write-Host "`n  logs:   $Log"
Write-Host   "  stop:   Stop-ScheduledTask -TaskName $TaskName"
Write-Host   "  start:  Start-ScheduledTask -TaskName $TaskName"
Write-Host   "  remove: .\install.ps1 -Uninstall`n"
