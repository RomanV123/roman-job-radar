# Scheduling Roman Job Radar

Two ways to run the pipeline automatically. Pick one — you don't need both.

(A third option, running on GitHub Actions, was tried and removed — GitHub's
runners have no persistent disk between runs, so it could never accumulate
real data or match what the dashboard shows, and it also required your
private `config/profile.yaml` to exist in the repo checkout, which
conflicts with that file being gitignored. Not worth the complexity for
what it could offer.)

## Option 1: Windows Task Scheduler (recommended — runs on your own PC)

This is the primary method: it keeps everything local (your resume and job
data never leave your machine), costs nothing, and matches the project's
"local storage by default" privacy goal. The only requirement is that your
PC needs to be on for a scheduled run to fire.

**Setup:**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup_task_scheduler.ps1
```

This registers a task named `RomanJobRadar` that runs `run_pipeline.py`
every 3 hours, indefinitely.

**Managing it:**

```powershell
Get-ScheduledTask -TaskName RomanJobRadar              # check it's registered
Start-ScheduledTask -TaskName RomanJobRadar             # run it right now
Get-ScheduledTaskInfo -TaskName RomanJobRadar           # last run time/result
Unregister-ScheduledTask -TaskName RomanJobRadar -Confirm:$false   # remove it
```

Or open **Task Scheduler** (`taskschd.msc`) and look under Task Scheduler
Library for a visual view — you can see run history, next run time, and
trigger a manual run from there too.

**Known limitation:** if your PC is asleep or off when a run is due, that
run is skipped — `-StartWhenAvailable` makes it fire as soon as the PC is
next on, rather than waiting for the next 3-hour mark, but a closed laptop
lid usually prevents Windows' wake-timer feature from working reliably.
This isn't a real problem for a job search: missing a few hours overnight
just means the next successful run picks up everything new since the last
one — nothing is lost.

## Option 2: cron (Linux/Mac alternative)

If you ever run this on a Linux/Mac machine, a NAS, or a small VPS instead:

```bash
crontab -e
```

Add:

```cron
0 */3 * * * cd /path/to/roman-job-radar && ./venv/bin/python run_pipeline.py >> logs/pipeline.log 2>&1
```

Create the `logs/` directory first (`mkdir -p logs`) since cron won't create
it for you, and the redirect will fail silently otherwise.

## Repeated-failure alerting

Regardless of which scheduling method you use, the pipeline itself watches
its own health: if 3 consecutive runs all collect zero jobs and record
errors, it sends a phone alert (via whichever alert provider is currently
active — see Phase 10) so you know something's actually broken, rather
than silently going stale. A handful of individual company failures mixed
in with real progress is normal (that's the whole point of per-source
isolation) and does **not** trigger this.
