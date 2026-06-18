# Badger — T+W Project Management Soul

Badger is a bespoke, soulful project management system for T+W. It sits on top of the
Google Sheets your PMs already live in, reads them live, and turns the raw plan into:

- **Badger Studio** — a clean Streamlit dashboard: calendar (day/week + resource overlay),
  resource workload views, project health, one-click reports, and a Slack preview/send centre.
- **Badger** — the persona/voice. Calm authority, short sentences, strong verbs, no fluff.
  Badger sends the daily Slack briefs and milestone reminders.
- **Intelligence layer** — `skills/` files (the "brain") that define how Badger thinks about
  scheduling, capacity, communication, and reporting.

**Single source of truth:** Google Sheets. PMs keep updating their familiar sheets. Badger
reads (and optionally writes back status), never fights the source.

---

## Project structure

```
badger/
├── app/
│   ├── badger_studio.py        # Main Streamlit app
│   ├── utils/
│   │   ├── data_parser.py      # Sheets/CSV -> clean DataFrames, utilization, grouping
│   │   ├── scheduler.py        # Workload calc, conflict detection, reallocation suggestions
│   │   ├── slack_sender.py     # Badger's Slack integration (slack_sdk)
│   │   ├── report_generator.py # Client milestone PDFs + internal capacity reports
│   │   └── calendar_utils.py   # Events for streamlit-calendar / fallback views
│   └── components/             # Reusable Streamlit components
├── skills/
│   ├── main_brain.md           # Core "soul" — how Badger thinks
│   └── organigram_resources.md # Living team directory (skills + capacity)
├── templates/
│   └── google_sheets_structure.md # Exact spec + sample for the input Sheet
├── data/                       # Sample CSVs / test data
├── reports/                    # Generated PDFs land here
├── requirements.txt
├── README.md
└── .streamlit/
    └── secrets.toml.example    # gsheets + slack tokens (copy to secrets.toml, then fill)
```

---

## Run it locally

You need Python 3.10+ installed. From the `badger/` folder:

```bash
pip install -r requirements.txt
streamlit run app/badger_studio.py
```

Streamlit opens the app in your browser at `http://localhost:8501`. With no connection
configured yet, you can use the CSV upload fallback (`data/sample_tasks.csv`, created in Phase 2).

---

## One-time setup: Google Sheets (non-technical, ~15 min)

This lets Badger read your Sheet automatically — no manual exports.

1. **Create a Google Cloud project.** Go to https://console.cloud.google.com → top bar →
   "Select a project" → "New Project" → name it `Badger` → Create.
2. **Turn on the APIs.** In the search bar type "Google Sheets API" → Enable. Repeat for
   "Google Drive API".
3. **Create a service account.** Left menu → "APIs & Services" → "Credentials" →
   "Create Credentials" → "Service account". Name it `badger-bot` → Done.
4. **Download the key.** Click the new service account → "Keys" tab → "Add Key" →
   "Create new key" → **JSON** → it downloads a file. Keep it safe.
5. **Share your Sheet with the bot.** Open the JSON file, copy the `client_email`
   (looks like `badger-bot@badger-xxxx.iam.gserviceaccount.com`). In your Master Tasks
   Google Sheet → Share → paste that email → give it **Editor** (or Viewer if read-only) → Send.
6. **Add the key to Badger.** Copy `.streamlit/secrets.toml.example` to
   `.streamlit/secrets.toml` and paste the values from the JSON file into the
   `[connections.gsheets]` block, plus your Sheet URL.

Done — Badger can now read the Sheet live.

---

## One-time setup: Slack app for Badger (~10 min)

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch** →
   name it `Badger` → pick your workspace.
2. Left menu → **OAuth & Permissions** → scroll to **Scopes** → **Bot Token Scopes** →
   add: `chat:write`, `chat:write.public`, `users:read`, `users:read.email`.
3. Scroll up → **Install to Workspace** → Allow.
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`).
5. Paste it into `.streamlit/secrets.toml` under `[slack] bot_token`.
6. (Optional) Under **Basic Information → Display Information**, set the app name to
   "Badger" and add an icon so messages feel like they come from Badger.
7. In the app, use **Test Badger Connection** (Admin tab, built in Phase 3) to send a hello.

---

## Deploy to Streamlit Community Cloud (free)

1. Push the `badger/` folder to a **private GitHub repo** (do NOT commit `secrets.toml`).
2. Go to https://share.streamlit.io → **New app** → connect the repo → set the main file to
   `app/badger_studio.py`.
3. In the app's **Settings → Secrets**, paste the full contents of your local
   `secrets.toml` (gsheets + slack). Save.
4. Deploy. You'll get a shareable URL for the team.

---

## Daily / weekly ritual

**Every morning (you / leadership)**
- Open Badger Studio → **Refresh Data** → review flags (overallocation, overdue, no-assignee).
- Open **Badger Comms Center** → generate daily briefs → preview → send via Slack.

**Whenever a PM updates the Sheet**
- Hit **Refresh** in Studio → workload and calendar update instantly.

**End of week**
- **Reports** → generate the Capacity Report (PDF) → review with the team/leadership.

**New project**
- PM adds rows to the Sheet → Refresh → review Badger's proposed schedule → adjust → Badger
  communicates kickoff.

---

## Build status

| Phase | What | Status |
|-------|------|--------|
| 1 | Folder skeleton, requirements, README | ✅ Done |
| 2 | data_parser, scheduler, calendar_utils, report_generator, slack_sender, badger_studio | ⏳ Next |
| 3 | Full integration, connections guide, deployment docs | ⏳ |
| 4 | Testing, real Sheet + team, go-live | ⏳ |

*Built with the Badger Playbook for T+W.*
