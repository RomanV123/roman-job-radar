<#
.SYNOPSIS
    Registers "RomanJobRadar" as a Windows Task Scheduler task that runs
    run_pipeline.py every 3 hours, indefinitely, on this PC.

.DESCRIPTION
    This is the primary scheduling method for Roman Job Radar — it keeps
    everything local (no cloud database, no committing SQLite to a repo)
    per the project's "local storage by default" privacy goal. The task
    only runs while this PC is on; -StartWhenAvailable makes a missed run
    (PC was asleep/off) fire as soon as the PC is next available, rather
    than being skipped entirely.

    Caveat: Windows' "wake the computer to run this task" feature is
    unreliable on laptops with the lid closed (modern standby often
    prevents it). Missing a few hours overnight isn't a real problem for a
    job search — the next run just picks up whatever's new since the last
    successful one.

.NOTES
    Run this from an elevated PowerShell prompt for best results:
        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
        .\scripts\setup_task_scheduler.ps1
#>

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $ProjectDir "run_pipeline.py"
$TaskName = "RomanJobRadar"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment not found at $PythonExe. Run 'python -m venv venv' and 'pip install -r requirements.txt' first."
    exit 1
}
if (-not (Test-Path $ScriptPath)) {
    Write-Error "run_pipeline.py not found at $ScriptPath."
    exit 1
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`"" -WorkingDirectory $ProjectDir

# [TimeSpan]::MaxValue produces an invalid Task Scheduler XML duration
# (P99999999DT23H59M59S) and makes Register-ScheduledTask fail — 10 years
# is effectively "indefinitely" for this purpose and is schema-valid.
$TriggerArgs = @{
    Once               = $true
    At                 = (Get-Date)
    RepetitionInterval = (New-TimeSpan -Hours 3)
    RepetitionDuration = (New-TimeSpan -Days 3650)
}
$Trigger = New-ScheduledTaskTrigger @TriggerArgs

$SettingsArgs = @{
    StartWhenAvailable  = $true
    DontStopOnIdleEnd   = $true
    WakeToRun           = $true
    # A full run across the whole company registry has taken up to ~1.5
    # hours at 500+ companies and will only grow as more are added -- the
    # original 1-hour cap was silently killing every single scheduled run
    # partway through (Task Scheduler reports this as "terminated by
    # user," easy to mistake for something else). 6 hours leaves real
    # headroom while still killing a genuinely hung run eventually.
    ExecutionTimeLimit  = (New-TimeSpan -Hours 6)
}
$Settings = New-ScheduledTaskSettingsSet @SettingsArgs

$RegisterArgs = @{
    TaskName    = $TaskName
    Action      = $Action
    Trigger     = $Trigger
    Settings    = $Settings
    Description = "Runs the Roman Job Radar job-search pipeline every 3 hours."
    Force       = $true
}

try {
    Register-ScheduledTask @RegisterArgs | Out-Null
} catch {
    Write-Error "Failed to register the scheduled task: $_"
    exit 1
}

# Don't just trust that Register-ScheduledTask didn't throw — confirm the
# task is actually queryable before reporting success.
$registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $registered) {
    Write-Error "Register-ScheduledTask reported no error, but the task isn't showing up in Task Scheduler. Something went wrong -- check manually with taskschd.msc."
    exit 1
}

Write-Host "Task '$TaskName' registered and confirmed -- it will run every 3 hours from now on."
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName                # check status"
Write-Host "  Start-ScheduledTask -TaskName $TaskName               # run it right now"
Write-Host "  Get-ScheduledTaskInfo -TaskName $TaskName             # last run time/result"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false   # remove it"
Write-Host ""
Write-Host "Or manage it visually: open Task Scheduler (taskschd.msc) and look under Task Scheduler Library."
