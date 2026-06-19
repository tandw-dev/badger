# Badger — T+W Project Management Soul

Badger is a bespoke, soulful project-management system for T+W. It sits on top of
the Google Sheet your PMs already live in, reads it live, and turns the raw plan
into a visual dashboard, intelligent resource scheduling, professional client
documents, and personality-driven communication on Slack.

This README is the single source of truth. Anyone on the team should be able to
run, use, deploy, and maintain Badger from this one file.

---

## What Badger is

- **Badger Studio** — a clean Streamlit dashboard: overview KPIs, calendar,
  resource-workload heatmaps, an editable task table (write-back to the Sheet),
  reports, and a Slack comms centre.
- **Badger** — the voice. Calm authority, short sentences, no fluff. Badger sends
  the daily briefs and milestone reminders, and signs the client documents.
- **The brain** — `skills/main_brain.md` (how Badger thinks) and
  `skills/organigram_resources.md` (the team, capacities, skills, Slack handles).
- **Single source of truth** — the Google Sheet "Badger's Project Management",
  one tab per project. PMs keep updating it; Badger reads (and writes back) — it
  never keeps its own competing copy.

---

## Project structure

```
badger/
├── app/
│   ├── badger_studio.py        # the dashboard (run this)
│   └── utils/
│       ├── data_parser.py      # Sheet/CSV <-> clean DataFrames (+ write-back)
│       ├── scheduler.py        # utilisation, flags, reallocation, briefs
│       ├── calendar_utils.py   # calendar events, heatmaps, bars, filters
│       ├── report_generator.py # client + capacity PDFs
│       └── slack_sender.py     # Badger's Slack voice
├── scripts/
│   └── daily_brief_job.py      # scheduled morning brief (cron / Actions / Make)
├── skills/
│   ├── main_brain.md
│   └── organigram_resources.md
├── templates/google_sheets_structure.md
├── data/sample_tasks.csv
├── reports/                    # generated PDFs land here
├── requirements.txt
├── README.md
└── .streamlit/
    ├── secrets.toml.example
    └── secrets.toml            # your real keys (never commit this)
```

---

## Run it locally

Python 3.10+ required. From the `badger/` folder:

```bash
pip install -r requirements.txt
streamlit run app/badger_studio.py
```

It opens at `http://localhost:8501`. With your secrets configured it loads the
live Sheet; with nothing configured it falls back to `data/sample_tasks.csv` so
you can explore every feature immediately (or use **Admin → Upload CSV**).

---

> **Fastest setup:** Badger Studio has a built-in **Setup Wizard** —
> open **Admin / Settings → Setup Wizard**, which also has a **Test Badger
> Connection** button that sends a real hello to Slack. The written steps below
> mirror it.

## One-time setup: Google Sheets (~15 min)

1. **console.cloud.google.com** → *Select a project* → **New Project** → name `Badger` → Create.
2. Search **Google Sheets API** → Enable. Repeat for **Google Drive API**.
3. **APIs & Services → Credentials → Create Credentials → Service account** → name `badger-bot` → Done.
4. Open the service account → **Keys → Add Key → Create new key → JSON** → download it. Keep it private.
5. Open the JSON, copy `client_email` (`…iam.gserviceaccount.com`). In your Master Tasks Sheet → **Share** → paste it → **Editor** → Send.
6. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, paste the JSON values into `[connections.gsheets]`, and add your Sheet URL.

## One-time setup: Slack (~10 min)

1. **api.slack.com/apps → Create New App → From scratch** → name `Badger` → your workspace.
2. **OAuth & Permissions → Bot Token Scopes**: add `chat:write`, `chat:write.public`, `users:read`, `users:read.email` (add `channels:read` to let Badger list channels).
3. **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-…`).
4. Paste it into `secrets.toml` under `[slack] bot_token`; set `default_channel`.
5. *(Optional)* **Basic Information → Display Information** → name it **Badger** + icon.
6. **Admin → Test Badger Connection** → send a hello.

> **Channel note:** Badger can DM any user, but to post to a *channel* it must be
> invited (`/invite @Badger` in that channel) or have the `chat:write.public` scope.

## One-time setup: Google Calendar (optional, ~10 min)

Lets Badger pull meetings into the Calendar view, subtract meeting time from
capacity in the Workload view, and mention the day's meetings in briefs. It uses
the **same service-account key** as Sheets.

1. In Google Cloud Console (same `Badger` project) → search **Google Calendar API** → **Enable**.
2. Choose how Badger reads calendars:
   - **Shared (simplest):** each person opens Google Calendar → **Settings → Settings for my calendars →** their calendar → **Share with specific people →** add the bot email `badger-bot@…iam.gserviceaccount.com` → permission **"See all event details"**. Repeat per person (or share one team/resource calendar).
   - **Delegated (whole team, needs Workspace admin):** Admin console → Security → API controls → Domain-wide delegation → add the service account's client ID with scope `https://www.googleapis.com/auth/calendar.readonly`. No per-calendar sharing needed.
3. In `secrets.toml` add the `[google_calendar]` block:
   ```toml
   [google_calendar]
   enabled = true
   mode = "shared"        # or "delegated"
   calendars = ["chris@tandw.co.za", "jack@tandw.co.za"]   # empty = whole team
   ```
4. In the app: **Admin → Google Calendar sync** → toggle on, pick mode, list calendars → **Test calendar sync** → **Apply & refresh**.

Meetings show grey on the Calendar, reduce effective capacity in Workload, and
appear in briefs ("You have 2.5h of meetings today — protect a focus block").
If a calendar isn't shared yet, Badger just skips it and tells you which.

---

## Deploy to Streamlit Community Cloud (free)

1. Push the `badger/` folder to GitHub (never commit `secrets.toml` — `.gitignore` already blocks it).
2. **share.streamlit.io → New app** → pick the repo → branch `main` → main file `app/badger_studio.py`.
3. **App settings → Secrets** → paste the *entire contents* of your local `secrets.toml` (the `[connections.gsheets]` and `[slack]` blocks). Save.
4. Deploy. You get a shareable `…streamlit.app` URL for the team. Later you can point a `badger.tandw.dev` subdomain at it.

Updating later: push to GitHub → Streamlit redeploys automatically. Note free
apps sleep after inactivity and wake in a few seconds on the next visit.

---

## Automation: automatic morning briefs

`scripts/daily_brief_job.py` runs Badger's morning routine headlessly. It has two
modes, and **starts in the safe one**:

- **Digest (default)** — posts ONE summary to a Slack channel for a human to
  glance at and approve. Sends nothing to individuals.
  `python scripts/daily_brief_job.py`
- **Send (`--send`)** — DMs each person their brief directly. Switch to this only
  once you trust the output.
  `python scripts/daily_brief_job.py --send`

**Recommended:** run digest mode for the first week or two (human approval gate),
then graduate to `--send` if you're happy.

### Option A — cron (a Mac/server that's always on)
```bash
# weekdays at 08:00 — edit paths to match your machine
0 8 * * 1-5 cd /path/to/badger && /usr/bin/python3 scripts/daily_brief_job.py >> reports/badger_cron.log 2>&1
```

### Option B — GitHub Actions (no server needed)
Add `.github/workflows/daily-brief.yml`:
```yaml
name: Badger daily brief
on:
  schedule:
    - cron: "0 6 * * 1-5"   # 06:00 UTC = 08:00 SAST, weekdays
  workflow_dispatch: {}
jobs:
  brief:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r badger/requirements.txt
      - name: Write secrets
        run: |
          mkdir -p badger/.streamlit
          printf '%s' "${{ secrets.BADGER_SECRETS_TOML }}" > badger/.streamlit/secrets.toml
      - run: cd badger && python scripts/daily_brief_job.py   # add --send when ready
```
Store your whole `secrets.toml` as a GitHub repo secret named `BADGER_SECRETS_TOML`.

### Option C — Make.com / Zapier
Schedule an HTTP/SSH step to run the job, or call the Slack API on a schedule
using briefs you export. Actions or cron are simpler and free.

> Streamlit Community Cloud itself can't run a background scheduler — drive the
> job from cron or Actions, not from inside the Studio app.

---

## Daily & weekly rituals

**Every morning (you / leadership)**
1. Open Badger Studio → **Refresh data**.
2. Scan **Overview**: KPIs, Badger's flags, and the Data Quality panel (fix anything listed, then Refresh).
3. **Badger's Daily Ritual** → review everyone's brief → go to **Badger Comms** → preview → send.

**Whenever a PM updates the Sheet**
- Hit **Refresh** → workload heatmap and calendar update instantly. If someone goes red, ask Badger to rebalance and test it in **What-if** before changing anything.

**End of week**
- **Reports → Capacity report** (PDF) → review with the team/leadership.

**New project**
- PM copies the project-tab template and adds rows → **Refresh** → review Badger's proposed schedule/flags → adjust in the task editor (**Save to Google Sheet**) → Badger communicates the kickoff via Comms.

**Maintenance**
- Keep `skills/organigram_resources.md` current (capacities, leave, Slack handles) — it's the foundation of fair resourcing. Edit it in **Admin → Organigram** and Save.

---

## Studio Schedule views (Calendar page)

The **Calendar** page has three tabs:

**🗓 Daily Resource View (primary)** — a Google-Calendar-style time grid for one day.
Left gutter is the clock; each column is a person from the organigram. Project
tasks appear as coloured blocks (by project) positioned by their **Time Started**
+ **Duration**; Google Calendar meetings appear in grey (dashed). Under each name:
`Xh task + Yh mtg / capacity`, flagged red when over a full day. Tasks without a
start time show as chips at the top of the column. Use the date picker/arrows to
change day, and the **"Edit this day's tasks"** expander to change status, hours,
person, times or notes — **Save** writes straight back to the Sheet.

**📊 Weekly Resource View** — people down the left, weekdays across the top. Each
cell is that person's total booked hours (tasks + meetings) for the day, traffic-lit
green/amber/red, with a `Xt+Ym` split and a hover tooltip. The right column totals
the week. Built for spotting bottlenecks at a glance.

**Classic calendar** — the original FullCalendar month/week view, kept as an
optional secondary view.

All three respect the global filters (person focus / projects). Utilisation now
**includes meeting time** — someone with 6h of meetings has only ~1h of project
capacity left, and the views show it.

### How the data flows
```
Google Sheet (tasks: Date + Time Started + Duration + Person)  ─┐
                                                                ├─► data_parser ─► tasks_df (with start_dt/end_dt)
Google Calendar (meetings: start/end per person)  ─► calendar_sync ─► events ─┘
                                                                │
        calendar_utils.build_daily_schedule / build_weekly_grid │  (merge + capacity maths)
                                                                ▼
        render_daily_grid_html / render_weekly_grid_html  ─►  Studio tabs
                                                                │
        edit a task in the Daily view ─► data_parser.write_back_tasks ─► back to the Sheet
```
The Sheet stays the single source of truth; meetings are read-only from Calendar.

## Talk to Badger

At the bottom of every page, ask in plain language:
*"who's overloaded?"*, *"rebalance Kholiwe"*, *"Busi's capacity"*,
*"brief for Entle"*, *"upcoming milestones"*, *"due today"*. Answers come straight
from the scheduler.

---

## Troubleshooting

- **`channel_not_found` on Slack** — invite `@Badger` to that channel, or DM a user ID instead.
- **Sheet shows no tasks** — the project tabs may be empty; add rows under the `TASKS` header, then Refresh. (Use the sample CSV to explore meanwhile.)
- **Write-back disabled** — you're in CSV mode; it only works against the live Sheet.
- **Capacity looks wrong** — check `organigram_resources.md`; unknown names default to 7h.
- **Secrets errors on deploy** — re-paste the full `secrets.toml` into Streamlit → App settings → Secrets.

---

## Roadmap (ideas, not yet built)

- **Deeper AI** — let "Talk to Badger" propose and *apply* multi-task rebalances; auto-draft client update emails per milestone.
- **Actual time tracking** — add an `Actual Hours` capture loop and compare estimate vs reality to sharpen future planning.
- **Client portal view** — a read-only milestone dashboard per client (e.g. on tandw.dev), fed by the same Sheet.
- **Status field in the Sheet** — add a `Status` column to the TASKS block for true status tracking + write-back.
- **Native fonts/branding** — drop T+W's Anonymous Pro into the PDFs via `add_font`.
- **Smart reminders** — Badger auto-pings on stalled tasks and pre-milestone, with a human approval gate.
- **Leave & holidays** — calendar-aware de-allocation from the organigram Notes.

---

## Build status

| Phase | What | Status |
|-------|------|--------|
| 1 | Skeleton, requirements, README | ✅ |
| 2 | parser · scheduler · calendar · reports · slack · dashboard | ✅ |
| 3 | Integration, Setup Wizard, deployment + automation docs | ✅ |
| 4 | Populate real tasks, go live, train PMs | ▶ in progress |

*Built with the Badger Playbook for T+W. Badger is listening.*
