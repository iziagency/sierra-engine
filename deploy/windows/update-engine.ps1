<#
    Sierra Engine auto-update.

    Runs on a schedule so a fix pushed to the public repo lands on this machine
    without anyone touching it. Pulls, and restarts the engine ONLY when the
    pull actually changed something - a restart drops the Slack connection for a
    second, so it is not done on a quiet check.

    ASCII only, same reason as install.ps1: Windows PowerShell 5.1 reads a .ps1
    with no BOM as Windows-1252, and a stray non-ASCII byte can end a string
    early and break parsing for a reason the error never explains.

    Registered by install.ps1 as the task 'SierraEngineUpdate'. Only meaningful
    on a git checkout; a ZIP extraction has no .git and this exits quietly.
#>
[CmdletBinding()]
param([string] $TaskName = 'SierraEngine')

$ErrorActionPreference = 'Stop'
$Repo    = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Watcher = Join-Path $Repo 'watcher'
$Log     = Join-Path $Watcher 'update.out.log'

function Note ($m) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -LiteralPath $Log -Value $line -Encoding ASCII
}

# A ZIP install has no repository to pull from. Say so once and stop - this is
# not an error, just a machine that opted out of auto-update by how it was set up.
if (-not (Test-Path (Join-Path $Repo '.git'))) {
    Note 'not a git checkout - auto-update skipped. Reinstall via git clone to enable.'
    exit 0
}

Set-Location $Repo

# git needs to be on PATH. A scheduled task gets a thin PATH, so fall back to the
# usual install location before giving up.
$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) {
    $guess = Join-Path $env:ProgramFiles 'Git\cmd\git.exe'
    if (Test-Path $guess) { $git = $guess }
}
if (-not $git) { Note 'git not found on PATH - cannot auto-update'; exit 1 }

$before = (& $git rev-parse HEAD 2>$null).Trim()
& $git fetch --quiet origin main 2>&1 | Out-Null
$remote = (& $git rev-parse origin/main 2>$null).Trim()

if (-not $remote) { Note 'could not reach origin - will try again next run'; exit 1 }
if ($before -eq $remote) { exit 0 }   # nothing new; leave the engine alone

Note ("update available: {0} -> {1}" -f $before.Substring(0,7), $remote.Substring(0,7))

# --ff-only: never create a merge commit on a client's server. If the local tree
# has somehow diverged, refuse and keep running the old code rather than guess.
$pull = & $git pull --ff-only origin main 2>&1
Note ($pull -join ' | ')
$after = (& $git rev-parse HEAD 2>$null).Trim()
if ($after -eq $before) { Note 'pull did not advance HEAD - engine left running on old code'; exit 1 }

# Clean restart. Stopping the scheduled task can leave the python child alive, and
# the single-instance lock would then refuse the new one - so kill the engine
# process explicitly and clear the lock, mirroring install.ps1 -Uninstall.
Note ("restarting engine at {0}" -f $after.Substring(0,7))
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*slack_engine*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item (Join-Path $Watcher 'engine.pid') -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $TaskName
Note 'restarted on the new code'
