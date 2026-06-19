"""
calendar_utils.py — view-prep layer for Badger Studio.

Turns the clean tasks_df from data_parser into exactly the shapes the dashboard
needs, so badger_studio.py stays thin:

  * Calendar events for the `streamlit-calendar` component (per task, coloured by
    project / person / status, with full detail in extendedProps).
  * Calendar resources (the people lanes for a resource/timeline view).
  * Workload HEATMAP data — person × date matrix of utilisation %, traffic-lit.
  * Workload BAR data — hours per person over a range.
  * Editable table prep for st.data_editor (display columns + column_config),
    plus a reverse-map so edits flow back through data_parser.write_back_tasks.
  * Filters: by person, project, date range, status — composable.

Colours follow the utilisation traffic-light idea from main_brain.md
(green healthy / amber watch / red overallocated) for the heatmap, and a stable
categorical palette for calendar events.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd

# Stable, readable categorical palette (kept muted/clean per T+W minimal style).
PALETTE = [
    "#2563eb", "#0d9488", "#7c3aed", "#db2777", "#ea580c",
    "#65a30d", "#0891b2", "#9333ea", "#c026d3", "#dc2626",
    "#4f46e5", "#059669", "#b45309", "#be123c", "#1d4ed8",
]
STATUS_COLORS = {
    "not started": "#9ca3af",
    "in progress": "#2563eb",
    "blocked": "#dc2626",
    "on hold": "#d97706",
    "done": "#16a34a",
}
# Heatmap traffic lights (utilisation bands)
BAND_COLORS = {"green": "#16a34a", "amber": "#f59e0b", "red": "#dc2626", "none": "#e5e7eb"}

# Columns shown (and edited) in the Projects & Tasks table.
EDITOR_COLUMNS = [
    "project", "task", "person", "est_hours",
    "start_date", "due_date", "status", "pct_complete", "notes",
]


# --------------------------------------------------------------------------- #
# Colour helpers
# --------------------------------------------------------------------------- #
def _stable_color(key: str, keys: list) -> str:
    """Deterministic colour per category so a project keeps its colour run-to-run."""
    try:
        idx = sorted(keys).index(key)
    except ValueError:
        idx = abs(hash(key))
    return PALETTE[idx % len(PALETTE)]


# --------------------------------------------------------------------------- #
# Filters (composable — call before any view builder)
# --------------------------------------------------------------------------- #
def apply_filters(tasks_df: pd.DataFrame,
                  persons: Optional[list] = None,
                  projects: Optional[list] = None,
                  date_range: Optional[tuple] = None,
                  statuses: Optional[list] = None) -> pd.DataFrame:
    """
    Return a filtered copy. `date_range` = (start_date, end_date) inclusive,
    matched against due_date. Any arg left None means 'no filter on that field'.
    """
    df = tasks_df.copy()
    if persons:
        df = df[df["person"].isin(persons)]
    if projects:
        df = df[df["project"].isin(projects)]
    if statuses:
        df = df[df["status"].isin(statuses)]
    if date_range and date_range[0] and date_range[1]:
        start, end = date_range
        df = df[df["due_date"].apply(
            lambda d: isinstance(d, date) and start <= d <= end)]
    return df


def filter_options(tasks_df: pd.DataFrame) -> dict:
    """Distinct values to populate the dashboard's filter widgets."""
    return {
        "persons": sorted([p for p in tasks_df["person"].dropna().unique() if str(p).strip()]),
        "projects": sorted([p for p in tasks_df["project"].dropna().unique() if str(p).strip()]),
        "statuses": sorted([s for s in tasks_df["status"].dropna().unique() if str(s).strip()]),
    }


# --------------------------------------------------------------------------- #
# Calendar events
# --------------------------------------------------------------------------- #
def build_calendar_events(tasks_df: pd.DataFrame, color_by: str = "project") -> list:
    """
    One event per task with a due_date. color_by in {project, person, status}.
    Title is scannable: "Person · Task (Xh)". Full detail rides in extendedProps
    so a click can show everything and offer 'send to Badger Slack'.
    """
    df = tasks_df.dropna(subset=["due_date"]).copy()
    if df.empty:
        return []
    proj_keys = list(df["project"].dropna().unique())
    person_keys = list(df["person"].dropna().unique())

    events = []
    for _, t in df.iterrows():
        if color_by == "status":
            color = STATUS_COLORS.get(str(t["status"]).lower(), "#6b7280")
        elif color_by == "person":
            color = _stable_color(t["person"], person_keys)
        else:
            color = _stable_color(t["project"], proj_keys)

        hours = f' ({t["est_hours"]:.0f}h)' if pd.notna(t["est_hours"]) else ""
        person = str(t["person"]).strip() or "Unassigned"
        start = t["start_date"] if isinstance(t["start_date"], date) else t["due_date"]
        events.append({
            "title": f'{person} · {t["task"]}{hours}',
            "start": t["due_date"].isoformat(),
            "end": t["due_date"].isoformat(),
            "allDay": True,
            "backgroundColor": color,
            "borderColor": color,
            "resourceId": person,
            "extendedProps": {
                "project": t["project"],
                "person": person,
                "hours": float(t["est_hours"]) if pd.notna(t["est_hours"]) else None,
                "status": t["status"],
                "client_review": bool(t.get("is_client_review", False)),
                "overdue": bool(t.get("is_overdue", False)),
                "notes": t.get("notes", ""),
                "start_date": start.isoformat() if isinstance(start, date) else None,
                "_tab": t["_tab"],
                "_row": int(t["_row"]),
            },
        })
    return events


def build_calendar_resources(tasks_df: pd.DataFrame,
                             organigram_df: Optional[pd.DataFrame] = None) -> list:
    """People lanes for streamlit-calendar's resource/timeline view."""
    people = set(p for p in tasks_df["person"].dropna().unique() if str(p).strip())
    if organigram_df is not None and not organigram_df.empty:
        people |= set(organigram_df["person"])
    return [{"id": p, "title": p} for p in sorted(people)]


def calendar_options(initial_view: str = "dayGridMonth",
                     resource_view: bool = False) -> dict:
    """Sensible default config for the streamlit-calendar component."""
    views = {
        "dayGridMonth": {"buttonText": "Month"},
        "timeGridWeek": {"buttonText": "Week"},
        "timeGridDay": {"buttonText": "Day"},
    }
    headerRight = "dayGridMonth,timeGridWeek,timeGridDay"
    if resource_view:
        initial_view = "resourceTimelineWeek"
        headerRight = "resourceTimelineWeek,resourceTimelineDay,dayGridMonth"
    return {
        "initialView": initial_view,
        "editable": False,           # editing happens in the table, not the calendar
        "selectable": True,
        "headerToolbar": {"left": "prev,next today", "center": "title", "right": headerRight},
        "views": views,
        "height": 720,
        "firstDay": 1,               # Monday
    }


# --------------------------------------------------------------------------- #
# Workload — heatmap data (person × date utilisation %)
# --------------------------------------------------------------------------- #
def workload_heatmap(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                     start: Optional[date] = None, end: Optional[date] = None) -> dict:
    """
    Return {'matrix': DataFrame(person × date of util%),
            'bands': DataFrame(same shape, 'green'/'amber'/'red'/'none'),
            'dates': [...], 'persons': [...]}.
    Designed to drop straight into a Plotly heatmap or a styled st.dataframe.
    """
    from scheduler import daily_utilization  # local import avoids circular cost

    daily = daily_utilization(tasks_df, organigram_df)
    if daily.empty:
        return {"matrix": pd.DataFrame(), "bands": pd.DataFrame(), "dates": [], "persons": []}

    if start is None:
        start = daily["date"].min()
    if end is None:
        end = daily["date"].max()
    all_dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    # weekdays only — cleaner for an agency week
    all_dates = [d for d in all_dates if d.weekday() < 5]

    persons = sorted(daily["person"].unique())
    matrix = pd.DataFrame(index=persons, columns=all_dates, dtype="float")
    for _, r in daily.iterrows():
        if r["date"] in matrix.columns:
            matrix.loc[r["person"], r["date"]] = r["utilization"]

    def band(v):
        if pd.isna(v):
            return "none"
        if v > 100:
            return "red"
        if v >= 85:
            return "amber"
        return "green"
    bands = matrix.apply(lambda col: col.map(band))
    return {"matrix": matrix, "bands": bands,
            "dates": all_dates, "persons": persons}


def build_heatmap_figure(heat: dict):
    """Optional: a Plotly heatmap from workload_heatmap() output. Returns a Figure or None."""
    if not heat or heat["matrix"].empty:
        return None
    import plotly.graph_objects as go
    m = heat["matrix"]
    z = m.values.astype(float)
    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=[d.strftime("%a %d %b") for d in m.columns],
        y=list(m.index),
        colorscale=[[0.0, "#dcfce7"], [0.6, "#fde68a"], [0.85, "#fca5a5"], [1.0, "#dc2626"]],
        zmin=0, zmax=150,
        hovertemplate="%{y} · %{x}<br>%{z:.0f}%<extra></extra>",
        colorbar={"title": "% util"},
    ))
    fig.update_layout(height=max(300, 28 * len(m.index) + 120),
                      margin={"l": 10, "r": 10, "t": 30, "b": 10},
                      title="Daily utilisation (%) — green ok · amber watch · red over")
    return fig


# --------------------------------------------------------------------------- #
# Workload — bars (hours per person over a range)
# --------------------------------------------------------------------------- #
def workload_bars(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                  start: Optional[date] = None, end: Optional[date] = None) -> pd.DataFrame:
    """
    Per-person totals over the range: allocated hours, weekday capacity in range,
    utilisation %, band. Ready for a bar chart or sorted table.
    """
    from scheduler import cap_for, _capacity_map
    caps = _capacity_map(organigram_df)
    df = tasks_df.dropna(subset=["due_date"]).copy()
    df = df[df["person"].astype(str).str.strip() != ""]
    if start and end:
        df = df[df["due_date"].apply(lambda d: start <= d <= end)]
    if df.empty:
        return pd.DataFrame(columns=["person", "allocated_hours", "capacity_in_range",
                                     "utilization", "band"])
    # weekday count in range for capacity scaling
    if start and end:
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        weekdays = sum(1 for d in days if d.weekday() < 5)
    else:
        span_dates = df["due_date"]
        weekdays = max(1, len(pd.bdate_range(span_dates.min(), span_dates.max())))

    g = df.groupby("person").agg(allocated_hours=("est_hours", "sum")).reset_index()
    g["capacity_in_range"] = g["person"].map(lambda p: cap_for(p, caps) * weekdays)
    g["utilization"] = (g["allocated_hours"] / g["capacity_in_range"] * 100).round(1)
    g["band"] = g["utilization"].apply(
        lambda u: "red" if u > 100 else ("amber" if u >= 85 else "green"))
    return g.sort_values("utilization", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Editable table for st.data_editor
# --------------------------------------------------------------------------- #
def to_editor_frame(tasks_df: pd.DataFrame) -> pd.DataFrame:
    """
    Slim, clean DataFrame for st.data_editor. Keeps the hidden _tab/_row columns
    so edits can be written back to the exact Sheet cells.
    """
    cols = EDITOR_COLUMNS + ["_tab", "_row"]
    present = [c for c in cols if c in tasks_df.columns]
    return tasks_df[present].copy().reset_index(drop=True)


def editor_column_config():
    """
    st.column_config for the task editor (dropdown status, date pickers, number
    fields, read-only project/provenance). Imported lazily so this module stays
    importable outside Streamlit.
    """
    import streamlit as st
    statuses = ["Not Started", "In Progress", "Blocked", "On Hold", "Done"]
    return {
        "project": st.column_config.TextColumn("Project", disabled=True),
        "task": st.column_config.TextColumn("Task", width="large"),
        "person": st.column_config.TextColumn("Person"),
        "est_hours": st.column_config.NumberColumn("Hours", min_value=0, step=0.5, format="%.1f"),
        "start_date": st.column_config.DateColumn("Start"),
        "due_date": st.column_config.DateColumn("Due"),
        "status": st.column_config.SelectboxColumn("Status", options=statuses),
        "pct_complete": st.column_config.NumberColumn("% Done", min_value=0, max_value=100, step=5),
        "notes": st.column_config.TextColumn("Notes", width="large"),
        "_tab": None,   # hidden
        "_row": None,   # hidden
    }


# --------------------------------------------------------------------------- #
# Daily "Studio View" — whole team's day at a glance
# --------------------------------------------------------------------------- #
def studio_day_view(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                    day: Optional[date] = None) -> list:
    """
    Grouped cards for one day: each person, their tasks, total hours vs capacity.
    Fallback/companion to the calendar for a clean 'today' glance.
    """
    from scheduler import cap_for, _capacity_map
    day = day or date.today()
    caps = _capacity_map(organigram_df)
    df = tasks_df[(tasks_df["due_date"] == day) &
                  (tasks_df["person"].astype(str).str.strip() != "")]
    cards = []
    for person, grp in df.groupby("person"):
        total = grp["est_hours"].sum(min_count=1)
        cap = cap_for(person, caps)
        cards.append({
            "person": person,
            "total_hours": round(float(total), 1) if pd.notna(total) else 0.0,
            "capacity": cap,
            "band": "red" if (total or 0) > cap else ("amber" if (total or 0) >= 0.85 * cap else "green"),
            "tasks": [{"task": r["task"], "project": r["project"],
                       "hours": float(r["est_hours"]) if pd.notna(r["est_hours"]) else None,
                       "status": r["status"]} for _, r in grp.iterrows()],
        })
    return sorted(cards, key=lambda c: -c["total_hours"])


# --------------------------------------------------------------------------- #
# Manual test:  python app/utils/calendar_utils.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(__file__))
    import data_parser as dp

    here = os.path.dirname(os.path.abspath(__file__))
    data = dp.load_all(csv_file=os.path.join(here, "..", "..", "data", "sample_tasks.csv"))
    org = dp.load_organigram()
    t = data.tasks_df

    print("=== calendar_utils self-test ===")
    ev = build_calendar_events(t, color_by="project")
    print(f"calendar events: {len(ev)}")
    print("  sample:", {k: ev[0][k] for k in ("title", "start", "backgroundColor")})

    res = build_calendar_resources(t, org)
    print(f"calendar resources (people lanes): {len(res)}")

    heat = workload_heatmap(t, org)
    print(f"heatmap matrix shape: {heat['matrix'].shape} (persons × weekdays)")

    bars = workload_bars(t, org)
    print("workload bars (top 3):")
    print(bars.head(3).to_string(index=False))

    opts = filter_options(t)
    print(f"filter options: {len(opts['persons'])} persons, {len(opts['projects'])} projects")
    f = apply_filters(t, projects=["Giants"])
    print(f"filter -> Giants only: {len(f)} tasks")

    ed = to_editor_frame(t)
    print(f"editor frame columns: {list(ed.columns)}")

    cards = studio_day_view(t, org, day=t['due_date'].dropna().iloc[0])
    print(f"studio day view cards: {len(cards)}")
