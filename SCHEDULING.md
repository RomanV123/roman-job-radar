# Scheduling Roman Job Radar

Three ways to run the pipeline automatically. Pick one — you don't need all three.

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

## Option 3: GitHub Actions (alternative — runs even when your PC is off)

See `.github/workflows/pipeline.yml`. It runs every 3 hours on GitHub's own
infrastructure via a `schedule` trigger, plus a `workflow_dispatch` trigger
for a manual "Run workflow" button in the Actions tab.

**The catch:** GitHub Actions runners are ephemeral — there's no persistent
disk between runs. The workflow uses `actions/cache` to carry
`data/job_radar.db` between runs as a pragmatic workaround, but that's not
true durable storage (cache entries can be evicted, especially if unused
for a while). If you want to actually rely on this long-term, swap
`DATABASE_URL` in `.env` to a free-tier hosted Postgres (Supabase or Neon
both work) — the app already runs on any SQLAlchemy URL, so it's a
one-line config change, not a code change.

To use this option:
1. Push this repo to GitHub (as a **private** repo — it contains your resume and profile).
2. Add `PUSHOVER_USER_KEY` and `PUSHOVER_APP_TOKEN` as repository secrets (Settings → Secrets and variables → Actions) if you want phone alerts from here.
3. The workflow runs automatically on schedule, or trigger it manually from the Actions tab.

## Repeated-failure alerting

Regardless of which scheduling method you use, the pipeline itself watches
its own health: if 3 consecutive runs all collect zero jobs and record
errors, it sends a phone alert (via whichever alert provider is currently
active — see Phase 10) so you know something's actually broken, rather
than silently going stale. A handful of individual company failures mixed
in with real progress is normal (that's the whole point of per-source
isolation) and does **not** trigger this.
