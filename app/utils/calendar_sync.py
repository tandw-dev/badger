"""
calendar_sync.py — Google Calendar integration for Badger.

Pulls real meetings from team calendars and merges them with project tasks so the
Studio can:
  * overlay meetings on the Calendar View (distinct grey/blue style),
  * subtract meeting time from each person's productive capacity in the Workload
    view (effective capacity = daily capacity - meeting hours),
  * mention the day's key meetings in Badger's Slack briefs.

Auth (service account — same JSON key as the Sheets connection):
  Two modes, set in secrets under [google_calendar]:

    mode = "shared"      (simplest)
        Each calendar you want to read is *shared* with the service-account
        email (badger-bot@…iam.gserviceaccount.com) in Google Calendar settings.
        `calendars` is the list of calendar IDs (usually the person's email).

    mode = "delegated"   (scales to the whole team, needs Workspace admin)
        The service account has domain-wide delegation with the Calendar
        readonly scope. Badger impersonates each person's email and reads their
        'primary' calendar — no per-calendar sharing needed.

Everything degrades gracefully: if it's not configured, a calendar can't be
read, or the API is off, the functions return empty results + a clear message,
and the rest of Badger keeps working.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, time
from typing import Optional

import pandas as pd

logger = logging.getLogger("badger.calendar_sync")

CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_calendar_config(secrets: Optional[dict] = None) -> dict:
    """
    Returns {'enabled', 'mode', 'calendars'}.
    `secrets` is the [google_calendar] block (e.g. st.secrets['google_calendar']).
    If omitted, tries local .streamlit/secrets.toml.
    """
    if secrets is None:
        secrets = _local_block("google_calendar") or {}
    return {
        "enabled": bool(secrets.get("enabled", False)),
        "mode": secrets.get("mode", "shared"),
        "calendars": list(secrets.get("calendars", []) or []),
    }


def _local_block(name: str) -> Optional[dict]:
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", ".streamlit", "secrets.toml")
    if not os.path.exists(path):
        return None
    try:
        try:
            import tomllib
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except ModuleNotFoundError:
            import tomli
            with open(path, "rb") as f:
                data = tomli.load(f)
        return data.get(name)
    except Exception:
        return None


def _gsheets_service_account() -> Optional[dict]:
    """Reuse the Sheets service-account JSON (same key works for Calendar)."""
    blk = _local_block("connections") or {}
    return (blk.get("gsheets") if isinstance(blk, dict) else None)


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #
def get_events(secrets_cal: Optional[dict] = None,
               sa_info: Optional[dict] = None,
               organigram_df: Optional[pd.DataFrame] = None,
               time_min: Optional[datetime] = None,
               time_max: Optional[datetime] = None) -> tuple:
    """
    Fetch events across the configured calendars for the window.
    Returns (events: list[dict], error: str|None).

    Each event dict:
      {calendar, person, person_email, title, start, end, date,
       duration_hours, all_day, attendees, html_link}
    `person` is resolved to the organigram name where possible.
    """
    cfg = load_calendar_config(secrets_cal)
    if not cfg["enabled"]:
        return [], "Calendar sync is off (enable it in Admin / secrets)."

    # calendars default to organigram emails if none listed
    calendars = cfg["calendars"]
    email_to_name = {}
    if organigram_df is not None and not organigram_df.empty:
        email_to_name = {str(e).strip().lower(): n
                         for n, e in zip(organigram_df["person"], organigram_df["email"])
                         if str(e).strip()}
        if not calendars:
            calendars = [e for e in email_to_name.keys()]

    if not calendars:
        return [], "No calendars configured to watch."

    sa = sa_info or _gsheets_service_account()
    if not sa:
        return [], "No service-account credentials found."

    now = datetime.utcnow()
    time_min = time_min or datetime.combine(date.today(), time.min)
    time_max = time_max or (time_min + timedelta(days=7))
    tmin = time_min.isoformat() + "Z"
    tmax = time_max.isoformat() + "Z"

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception as e:
        return [], f"Calendar libraries not installed: {e}"

    events, errors = [], []
    for cal_id in calendars:
        try:
            creds = service_account.Credentials.from_service_account_info(sa, scopes=CAL_SCOPES)
            if cfg["mode"] == "delegated":
                creds = creds.with_subject(cal_id)          # impersonate the user
                target = "primary"
            else:
                target = cal_id                              # read the shared calendar
            svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
            resp = svc.events().list(calendarId=target, timeMin=tmin, timeMax=tmax,
                                     singleEvents=True, orderBy="startTime",
                                     maxResults=250).execute()
            for ev in resp.get("items", []):
                parsed = _parse_event(ev, cal_id, email_to_name)
                if parsed:
                    events.append(parsed)
        except Exception as e:
            errors.append(f"{cal_id}: {str(e)[:120]}")
            logger.warning("Calendar fetch failed for %s: %s", cal_id, e)

    err = None
    if errors:
        err = "Some calendars couldn't be read — " + " | ".join(errors[:5])
    return events, err


def _parse_event(ev: dict, cal_id: str, email_to_name: dict) -> Optional[dict]:
    start = ev.get("start", {})
    end = ev.get("end", {})
    all_day = "date" in start
    try:
        if all_day:
            s = datetime.fromisoformat(start["date"])
            e = datetime.fromisoformat(end["date"])
            dur = 0.0  # all-day events don't consume productive hours
            d = s.date()
        else:
            s = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
            dur = round((e - s).total_seconds() / 3600.0, 2)
            d = s.date()
    except Exception:
        return None
    if ev.get("status") == "cancelled":
        return None
    name = email_to_name.get(str(cal_id).strip().lower(), cal_id)
    attendees = [a.get("email", "") for a in ev.get("attendees", []) if a.get("email")]
    return {
        "calendar": cal_id,
        "person": name,
        "person_email": cal_id,
        "title": ev.get("summary", "(no title)"),
        "start": s,
        "end": e,
        "date": d,
        "duration_hours": dur,
        "all_day": all_day,
        "attendees": attendees,
        "html_link": ev.get("htmlLink", ""),
    }


# --------------------------------------------------------------------------- #
# Aggregation + merge helpers
# --------------------------------------------------------------------------- #
def meeting_hours_by_person_day(events: list) -> dict:
    """{(person, date): total meeting hours} — timed events only."""
    out = {}
    for ev in events:
        if ev["all_day"]:
            continue
        out[(ev["person"], ev["date"])] = out.get((ev["person"], ev["date"]), 0.0) + ev["duration_hours"]
    return out


def meeting_hours_for(events: list, person: str, day: date) -> float:
    return round(sum(ev["duration_hours"] for ev in events
                     if ev["person"] == person and ev["date"] == day and not ev["all_day"]), 2)


def calendar_overlay_events(events: list) -> list:
    """streamlit-calendar event dicts for meetings — distinct grey/blue style."""
    MEET_BG = "#64748b"   # slate — clearly different from project task colours
    out = []
    for ev in events:
        when = "" if ev["all_day"] else ev["start"].strftime("%H:%M")
        title = f'📅 {ev["title"]}' + (f' ({when})' if when else "")
        out.append({
            "title": f'{ev["person"]} · {title}',
            "start": ev["start"].isoformat(),
            "end": ev["end"].isoformat(),
            "allDay": ev["all_day"],
            "backgroundColor": MEET_BG,
            "borderColor": MEET_BG,
            "display": "block",
            "extendedProps": {
                "type": "meeting",
                "person": ev["person"],
                "attendees": ev["attendees"],
                "hours": ev["duration_hours"],
                "link": ev["html_link"],
            },
        })
    return out


def workload_with_meetings(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                           events: list, day: date) -> pd.DataFrame:
    """
    Per-person view for `day` with meetings subtracted from capacity:
      task_hours, meeting_hours, capacity, effective_capacity, utilization, band.
    Effective capacity floors at 0.5h so we never divide by zero.
    """
    from scheduler import _capacity_map, cap_for
    caps = _capacity_map(organigram_df)
    mh = meeting_hours_by_person_day(events)

    # task hours due that day, per person
    t = tasks_df[(tasks_df["due_date"] == day) &
                 (tasks_df["person"].astype(str).str.strip() != "")]
    task_hours = t.groupby("person")["est_hours"].sum(min_count=1).to_dict()

    people = set(task_hours) | {p for (p, d) in mh if d == day}
    rows = []
    for p in sorted(people):
        th = float(task_hours.get(p, 0) or 0)
        m = float(mh.get((p, day), 0))
        cap = cap_for(p, caps)
        eff = max(0.5, cap - m)
        util = round(th / eff * 100, 1)
        band = "red" if util > 100 else ("amber" if util >= 85 else "green")
        rows.append({"person": p, "task_hours": round(th, 2), "meeting_hours": round(m, 2),
                     "capacity": cap, "effective_capacity": round(eff, 2),
                     "utilization": util, "band": band})
    return pd.DataFrame(rows)


def meetings_brief_line(events: list, person: str, day: date) -> str:
    """A one-line meeting note for Badger's daily brief, or '' if none."""
    todays = [e for e in events if e["person"] == person and e["date"] == day and not e["all_day"]]
    if not todays:
        return ""
    total = round(sum(e["duration_hours"] for e in todays), 1)
    titles = ", ".join(e["title"] for e in todays[:3])
    note = f"📅 You have {total}h of meetings today ({titles})."
    if total >= 3:
        note += " Protect a focus block for the project work above."
    return note


def test_connection(secrets_cal: Optional[dict] = None, organigram_df: Optional[pd.DataFrame] = None) -> dict:
    """Quick check: try to read the next 24h across configured calendars."""
    evs, err = get_events(secrets_cal, organigram_df=organigram_df,
                          time_min=datetime.utcnow(),
                          time_max=datetime.utcnow() + timedelta(days=1))
    return {"ok": err is None, "events": len(evs), "error": err}


# --------------------------------------------------------------------------- #
# Manual test (degrades gracefully without config):
#   python app/utils/calendar_sync.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(__file__))
    import data_parser as dp

    org = dp.load_organigram()
    print("=== calendar_sync self-test ===")
    cfg = load_calendar_config()
    print("config:", cfg)
    evs, err = get_events(organigram_df=org)
    print("events:", len(evs), "| note:", err)

    # logic test with MOCK meetings (proves merge/effective-capacity without live API)
    print("\n-- mock meeting merge --")
    today = date.today()
    mock = [
        {"calendar": "chris@tandw.co.za", "person": "Chris Green", "person_email": "chris@tandw.co.za",
         "title": "SuperSport call", "start": datetime.combine(today, time(10)),
         "end": datetime.combine(today, time(12)), "date": today, "duration_hours": 2.0,
         "all_day": False, "attendees": ["jack@tandw.co.za"], "html_link": "http://cal"},
        {"calendar": "chris@tandw.co.za", "person": "Chris Green", "person_email": "chris@tandw.co.za",
         "title": "Standup", "start": datetime.combine(today, time(9)),
         "end": datetime.combine(today, time(9, 30)), "date": today, "duration_hours": 0.5,
         "all_day": False, "attendees": [], "html_link": ""},
    ]
    csv = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sample_tasks.csv")
    tasks = dp.load_all(csv_file=csv).tasks_df
    # pretend Chris has tasks today for the demo
    wl = workload_with_meetings(tasks, org, mock, today)
    print(wl.to_string(index=False) if not wl.empty else "(no overlap today)")
    print("\nbrief line:", meetings_brief_line(mock, "Chris Green", today))
    print("overlay events:", len(calendar_overlay_events(mock)))
