# Roman Job Radar

A personal job-search application that continuously scans permitted public
job boards (Greenhouse, Lever, Ashby, Workday, USAJOBS, and select company
career pages), scores every listing against Roman's resume, and surfaces
high-quality matches on a local dashboard and via phone notification —
**without ever automatically applying to anything.**

It searches nationwide with a California priority boost, covers full-time
roles and internships, and runs entirely on your own machine: your resume
never leaves it, and no external LLM is ever involved in scoring a job.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Architecture](#2-architecture)
3. [Installation](#3-installation)
4. [Adding your resume](#4-adding-your-resume)
5. [Editing your profile](#5-editing-your-profile)
6. [Adding companies](#6-adding-companies)
7. [Identifying ATS board identifiers](#7-identifying-ats-board-identifiers)
8. [Configuring Pushover](#8-configuring-pushover)
9. [Running a dry search](#9-running-a-dry-search)
10. [Sending a test alert](#10-sending-a-test-alert)
11. [Enabling scheduled runs](#11-enabling-scheduled-runs)
12. [Launching the dashboard](#12-launching-the-dashboard)
13. [How scoring works](#13-how-scoring-works)
14. [Privacy and scraping limitations](#14-privacy-and-scraping-limitations)
15. [Troubleshooting](#15-troubleshooting)
16. [Docker deployment (optional)](#16-docker-deployment-optional)

---

## 1. What it does

- Collects full-time and internship listings from Greenhouse, Lever, Ashby,
  Workday, and USAJOBS (federal government) public APIs, plus permitted
  custom career pages — never LinkedIn or sites that prohibit automated
  collection.
- Normalizes wildly inconsistent postings into a common shape: title,
  location/state, workplace type (remote/hybrid/onsite), employment type,
  salary, required experience, and posted date.
- Deduplicates the same job appearing under a different ID, URL, or
  re-scrape.
- Filters out jobs you're objectively not eligible for (Senior/Staff/
  Director titles, jobs requiring 3+ years, non-U.S. locations, contract-only
  roles) — without discarding jobs where you meet *most but not all*
  preferred qualifications.
- Scores every remaining job deterministically against your profile —
  skills, experience, title fit, education, location, semantic similarity,
  and freshness — and explains *why* it matched, what skills you have, and
  what you're missing.
- Shows everything on a local Streamlit dashboard with save/apply-tracking/
  notes, and optionally pings your phone via Pushover for standout matches.
- **Never submits an application on your behalf.** Every "Apply" action is
  a link to the company's own application page, opened in a new tab.

## 2. Architecture

```
resume.pdf ──► src/resume/parser.py ──► (you edit) config/profile.yaml
                                                          │
config/companies.yaml ──► src/collectors/* ──► RawJob    │
                                          │               │
                                          ▼               ▼
                              src/processing/normalize.py │
                                          │               │
                                          ▼               │
                             src/processing/deduplicate.py│
                                          │               │
                                          ▼               │
                             src/processing/eligibility.py◄
                                          │
                                          ▼
                                src/matching/scorer.py ◄── local sentence-transformer
                                          │                (semantic similarity only —
                                          ▼                 never an external LLM call)
                             SQLite (data/job_radar.db)
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                 ▼
                    app.py         src/alerts/*        Pipeline Health
                 (Streamlit)   (Pushover/console)      page in app.py
```

All of the above is orchestrated by `src/services/pipeline.py`, which
`run_pipeline.py` (CLI) and the dashboard's "Refresh data now" button both
call into. The architecture is deliberately layered so pieces are
swappable later without a rewrite: SQLite → PostgreSQL is a one-line
`DATABASE_URL` change (see `src/database/session.py`), and Streamlit → a
React/Next.js frontend would only touch `app.py` and
`src/services/dashboard_data.py`, not the collection/scoring core.

Directory layout:

```
roman-job-radar/
├── app.py                    # Streamlit dashboard
├── run_pipeline.py           # CLI entry point
├── config/
│   ├── profile.yaml          # YOUR editable candidate profile (source of truth)
│   ├── settings.yaml         # scoring weights, location tiers, role categories
│   ├── companies.yaml        # company registry (157 live-verified seed entries)
│   ├── skills.yaml           # skill vocabulary + aliases
│   └── title_aliases.yaml    # job title normalization map
├── src/
│   ├── collectors/           # Greenhouse, Lever, Ashby, Workday, government, custom
│   ├── processing/           # normalize, deduplicate, eligibility, expire_jobs
│   ├── matching/             # skill_matcher, semantic_matcher, scorer, explanation
│   ├── alerts/                # pushover, console, digest
│   ├── database/             # SQLAlchemy models + session management
│   ├── resume/                # PDF parsing + profile loading
│   └── services/              # pipeline orchestration, dashboard queries
├── data/job_radar.db         # SQLite database (gitignored)
└── tests/                    # 300+ tests, all mocked — no live network needed
```

## 3. Installation

```bash
python -m venv venv
venv\Scripts\activate           # Windows; use `source venv/bin/activate` on Mac/Linux
pip install -r requirements.txt
copy .env.example .env          # `cp` on Mac/Linux
pytest                          # confirm everything passes before you start
```

The first time anything calls the semantic matcher (Phase 8 scoring), it
downloads a small (~90MB) sentence-transformer model from Hugging Face.
This needs network access **once** — after that, it's cached locally and
never touches the network again (see [Troubleshooting](#15-troubleshooting)
if this seems to hang).

## 4. Adding your resume

Place your resume PDF at the project root as `resume.pdf` (gitignored —
it's never committed). This project does **not** auto-generate your
profile from the PDF — resume parsing is a one-way reference tool, not a
source of truth, so a bad OCR extraction can't silently corrupt your
profile:

```bash
python run_pipeline.py --extract-resume-text
```

This writes the extracted raw text to `data/resume_raw.txt` (also
gitignored) purely as a reference while you edit `config/profile.yaml`
yourself. If your resume changes materially (new job, new skills), update
`profile.yaml` directly — that's the file everything else reads from.

## 5. Editing your profile

`config/profile.yaml` is hand-maintained and is what scoring actually
reads — correcting it is the main way you influence match quality.
It's gitignored (it holds your real name/email/phone), so on a fresh
clone copy the tracked template first:

```bash
cp config/profile.example.yaml config/profile.yaml
```

Then edit `config/profile.yaml` with your real contact info and resume
details. Key sections:

- **`education`** / **`experience`** — used for the education-fit score and
  for "relevant experience" shown on each job card. Each experience entry's
  `skills_demonstrated` list drives which resume bullets get cited as
  "relevant" for a given match.
- **`skills.primary`** / **`skills.ot_manufacturing`** — must be canonical
  names from `config/skills.yaml` (see below) — free-text skills you add
  here that aren't in that vocabulary simply won't be matched against job
  postings.
- **`target_role_categories`** / **`target_titles_freeform`** — drives the
  title-fit score and the "Role category" dashboard filter. Add a title
  here if you want it explicitly recognized as a target.
- **`eligibility`** — `citizenship_restricted_roles_eligible`,
  `graduate_student_status`, and `max_years_experience_have` directly feed
  eligibility filtering and experience scoring. Keep these current.

To add a new skill to the recognized vocabulary (so it can actually be
matched against postings), add it to `config/skills.yaml` first, with any
common aliases (e.g. "AWS" as an alias for "Amazon Web Services"), then
reference the canonical name in `profile.yaml`.

## 6. Adding companies

`config/companies.yaml` ships with 157 companies, each **live-verified**
against the real API at build time — not guessed. To add your own:

```yaml
- name: Example Co
  industry: cybersecurity        # free text — biotech, cybersecurity, ot, technology, ...
  ats_type: greenhouse           # greenhouse | lever | ashby | workday | government | custom
  board_identifier: examplecoinc
  careers_url: https://example.com/careers   # used by government/custom sources
  priority: 1                    # 1 = poll hourly, 2 = every 3h (informational; see SCHEDULING.md)
  active: true
```

`custom_config` is needed for three source types:
- **Workday**: `{wd_host: wd3, site: Lonza_Careers}` (see [§7](#7-identifying-ats-board-identifiers))
- **Government (USAJOBS)**: `{keyword: "cybersecurity", organization: "DHS", location_name: "Sacramento, CA"}`
- **Custom pages**: CSS selectors — `{job_selector: "div.job-listing", title_selector: "h3.job-title", link_selector: "a.job-link", location_selector: "span.job-location"}`

**Only add a `custom` entry for a page whose terms/robots.txt actually
permit automated access.** This project deliberately never scrapes
LinkedIn or similar sites that prohibit it.

Companies that don't resolve against any of these ATS types (many large
enterprises run Workday, SuccessFactors, or Phenom People with no public
API) are documented in the "review queue" comment block at the bottom of
`companies.yaml` rather than silently guessed at.

## 7. Identifying ATS board identifiers

**Greenhouse / Lever / Ashby** — the identifier is usually the company's
slug. Confirm it by requesting the public endpoint directly and checking
for a real jobs array:

```bash
curl https://boards-api.greenhouse.io/v1/boards/<guess>/jobs
curl "https://api.lever.co/v0/postings/<guess>?mode=json"
curl "https://api.ashbyhq.com/posting-api/job-board/<guess>"
```

A `200` response with a populated jobs list confirms the slug. A `404`
means guess again — common variants are the company name with/without
spaces, hyphens, or a trailing "inc"/"hq".

**Workday** is different: it has no predictable slug, and needs both a
`wd_host` (wd1–wd5) and a `site` name in `custom_config`. To find them,
open the company's actual careers page in a browser, open DevTools →
Network, and look for a request to
`https://<tenant>.wd<N>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs` —
that URL contains all three values you need. You can also brute-force it:

```bash
curl -X POST "https://<tenant>.wd1.myworkdayjobs.com/wday/cxs/<tenant>/<guess-site>/jobs" \
  -H "Content-Type: application/json" \
  -d '{"appliedFacets":{},"limit":1,"offset":0,"searchText":""}'
```

Try `wd1` through `wd5` and common site names (`External`, `Careers`,
`<Company>_Careers`, `<Company>Careers`) until one returns real job data.

**USAJOBS (government)** requires a free API key from
[developer.usajobs.gov](https://developer.usajobs.gov/) — set
`USAJOBS_API_KEY` and `USAJOBS_EMAIL` in `.env`. State/local government
agencies (CalCareers, etc.) generally have no equivalent public API and
aren't currently supported.

## 8. Configuring notifications (Pushover and/or email)

Two independent channels are supported — set up either one, or both (a
match then gets sent to every configured channel). Neither is required to
use the rest of the app.

**Pushover (phone push notifications):**

1. Create a free account at [pushover.net](https://pushover.net) and
   install the app on your phone.
2. Create an "Application" in the Pushover dashboard to get an app token.
3. Add both values to `.env`:
   ```
   PUSHOVER_USER_KEY=your_user_key
   PUSHOVER_APP_TOKEN=your_app_token
   ```

**Email:**

1. Decide which account will *send* the emails (it doesn't need to be the
   same as the destination address). Get its SMTP settings — for example,
   Outlook/Hotmail uses `smtp-mail.outlook.com` port `587`, Gmail uses
   `smtp.gmail.com` port `587`.
2. If that sending account has 2FA enabled (most do), generate an **app
   password** for it — most providers require this for SMTP login instead
   of your regular password.
3. Add to `.env`:
   ```
   SMTP_HOST=smtp-mail.outlook.com
   SMTP_PORT=587
   SMTP_USERNAME=your_sending_account@outlook.com
   SMTP_PASSWORD=your_app_password
   EMAIL_FROM=your_sending_account@outlook.com
   EMAIL_TO=where_you_want_alerts@example.com
   ```

**Then, regardless of which channel(s) you set up:**

4. [Send a test alert](#10-sending-a-test-alert) and confirm it actually
   arrives (phone and/or inbox).
5. Only then set `ENABLE_NOTIFICATIONS=true` in `.env`. Until you do, all
   alerts print to the console/log instead of being sent — this is
   intentional (see `src/alerts/__init__.py`'s `get_alert_provider`), so a
   half-configured setup can never accidentally spam you.

## 9. Running a dry search

```bash
python run_pipeline.py --dry-run
```

Collects and evaluates eligibility against every active company, but
writes nothing to the database and sends no alerts. Combine with
`--company`/`--source` to test a specific piece:

```bash
python run_pipeline.py --dry-run --company "Cloudflare"
```

## 10. Sending a test alert

```bash
python run_pipeline.py --send-test-alert
```

Sends a real test notification to every channel you've configured
(Pushover and/or email) using your `.env` credentials, **regardless of
`ENABLE_NOTIFICATIONS`** — that flag only gates the pipeline's automatic
alerts, not this manual check. Reports success/failure per channel. There's
also a "Send test notification" button on the dashboard's Settings page
that does the same thing.

## 11. Enabling scheduled runs

See **[SCHEDULING.md](SCHEDULING.md)** for full details. Short version:
Windows Task Scheduler is the primary, recommended method (keeps
everything local, matching this project's privacy design) —

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup_task_scheduler.ps1
```

registers a task that runs `run_pipeline.py` every 3 hours. cron
(Linux/Mac) is documented there too as an alternative.

## 12. Launching the dashboard

```bash
streamlit run app.py
```

Opens at `http://localhost:8501` with 11 pages: Recommended Jobs,
California Jobs, Nationwide Jobs, Remote Jobs, Internships, Full-Time
Jobs, Biotech and OT, Saved Jobs, Application Tracker, Settings, and
Pipeline Health. The Settings page also has "Refresh data now" (run the
pipeline against a chosen subset of companies), "Send test notification,"
and "Delete all data."

## 13. How scoring works

Every eligible job gets a deterministic 0–100 score — **no LLM ever
invents the number.** It's a weighted sum of seven sub-scores
(`config/settings.yaml`'s `scoring_weights`):

| Dimension | Weight | How it's computed |
|---|---|---|
| Skills | 30% | Overlap between your `profile.yaml` skills and skills mentioned in the posting's required/preferred text — with a dampener so one incidental keyword can't outscore several genuine matches |
| Experience | 20% | Your years vs. the posting's *required* (not preferred) minimum |
| Title | 15% | Fuzzy match against your target titles/categories |
| Education | 10% | Degree requirement vs. your completed/in-progress degrees |
| Location | 10% | California = full score, remote-US = high, other US = moderate |
| Semantic | 10% | Local sentence-transformer cosine similarity between your resume and the job text — catches paraphrased matches that keyword-matching misses |
| Freshness | 5% | Newer postings score higher within the lookback window |

Score bands: **88–100 exceptional, 80–87 strong, 70–79 good, 60–69
possible, below 60 hidden by default** (adjustable via the dashboard's
"Minimum score" filter — hiding is a display default, not a data-level
deletion). Certain skill combinations (e.g. Splunk + SIEM + network
traffic, Palo Alto NGFW + Panorama + firewall policies) get a bonus,
configured in `settings.yaml`'s `skill_combinations_bonus`.

Every job card also shows matching skills, missing required vs. preferred
skills (never a skill you don't actually have — that's structurally
guaranteed, not just a convention), relevant resume experience, and any
eligibility concerns (e.g. "may require a security clearance") that didn't
rise to an outright rejection.

## 14. Privacy and scraping limitations

- **Local by default.** SQLite database, local sentence-transformer model
  — your resume and profile never leave your machine unless you
  explicitly push this project's data elsewhere yourself.
- **No external LLM is ever used**, for scoring or anything else. Semantic
  similarity runs on a small local model; there is no code path that sends
  resume or job content to OpenAI, Anthropic, or any other API.
- **Never scrapes LinkedIn or any site that prohibits automated
  collection.** Only official public APIs (Greenhouse, Lever, Ashby,
  Workday's own search endpoint, USAJOBS) and explicitly configured pages
  you've confirmed permit it.
- **No system can guarantee every job on the internet.** Coverage is
  bounded by which companies are in `companies.yaml` and which ATS
  platforms are supported — expand it by adding more companies.
- **Never applies automatically.** Every "Apply" is a link you click
  yourself, on the company's own site.
- **`python run_pipeline.py --delete-all-data`** (or the dashboard's
  Settings page) permanently wipes every collected job, match,
  application, and pipeline run — but never your hand-authored
  `profile.yaml`, `companies.yaml`, or `resume.pdf`.
- Secrets live only in `.env` (gitignored); request timeouts and
  parameterized queries are used throughout; see the Phase 13 audit notes
  in git history for the full checklist.

## 15. Troubleshooting

**`ValueError: ENABLE_NOTIFICATIONS=true requires PUSHOVER_USER_KEY...`**
You flipped the flag before setting both Pushover values in `.env`. Fill
them in, or set the flag back to `false` until you're ready.

**Semantic scoring seems to hang or is very slow.**
The very first run needs network access to download the sentence-transformer
model (~90MB, one-time). After that it loads from cache in a couple of
seconds — if it's still slow every run, check nothing is deleting your
Hugging Face cache (`~/.cache/huggingface`) between runs.

**A company returns 0 jobs / collector fails.**
Run `python run_pipeline.py --dry-run --company "Name"` to isolate it. Most
often the `board_identifier` (or Workday `wd_host`/`site`) is wrong — recheck
using [§7](#7-identifying-ats-board-identifiers). One company failing never
stops the rest of the run (per-source isolation) — check `errors` in the
Pipeline Health dashboard page or the CLI's log output.

**Getting a phone alert for every single job, or none at all.**
Alerts only fire for jobs *newly created* in that run — a re-scraped
existing job just updates `last_seen_at`, it doesn't re-alert. If you're
getting nothing, confirm `ENABLE_NOTIFICATIONS=true` and that
`--send-test-alert` succeeds first.

**"Every requested company failed to collect" / repeated-failure alert.**
Check your network connection, and whether an ATS changed its API shape
(rare, but it happens). `run_pipeline.py` exits with code 1 in this case
so cron/Task Scheduler can flag it as a failed run.

**Streamlit dashboard shows stale/no data.**
It reads directly from `data/job_radar.db` — run the pipeline (CLI or the
dashboard's "Refresh data now") to populate or update it.

**Database is locked.**
SQLite doesn't handle concurrent writers well — don't run the CLI pipeline
and a dashboard-triggered refresh at the same time.

**Tests fail with a network-related error.**
They shouldn't — the entire suite is mocked (`respx`) and verified to pass
with network access completely severed. If something's genuinely reaching
the network, that's a bug — please report which test.

---

## 16. Docker deployment (optional)

Not required for normal use — everything above runs directly via `venv`,
and Windows Task Scheduler (§11) is the primary scheduling method. This is
an alternative for running the dashboard on a home server/NAS instead of
your main PC.

**Note:** this Dockerfile/compose setup was written but could not be
build-tested in the environment this project was developed in (no Docker
available there) — verify it works with `docker compose build` before
relying on it, and please flag anything broken.

`.env` and `resume.pdf` must already exist (§3–4) before building — both
are bind-mounted at runtime, never baked into the image:

```bash
docker compose build
docker compose up dashboard              # dashboard at http://localhost:8501
docker compose run --rm pipeline         # one-off pipeline run
docker compose run --rm pipeline python run_pipeline.py --dry-run
```

The `pipeline` service isn't started by `docker compose up` — it has no
default profile, so running it (once or on a schedule via host cron
calling `docker compose run --rm pipeline`) is a deliberate action, not
automatic. The sentence-transformer model is downloaded once at *build*
time so the running container never needs network access to score jobs.

---

## Project status

Everything above is built and tested (306 tests, all mocked, verified to
pass with network access completely severed). The Docker setup (§16) is
written but unverified — no Docker was available in the environment this
project was built in, so build-test it yourself before relying on it.
Everything else has been exercised against real, live data at each phase,
not just unit tests.
