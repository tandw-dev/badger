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


# --------------------------------------------------------------------------- #
# RESOURCE SCHEDULING VIEWS — Daily time-grid + Weekly grid (tasks + meetings)
# --------------------------------------------------------------------------- #
WORK_START_H = 7    # default visible window
WORK_END_H = 19
MEET_COLOR = "#64748b"   # slate — meetings

def _mins(dt) -> int:
    return dt.hour * 60 + dt.minute

def build_daily_schedule(tasks_df: pd.DataFrame, events: list,
                         organigram_df: pd.DataFrame, day: date,
                         day_start_h: int = WORK_START_H, day_end_h: int = WORK_END_H,
                         include_all: bool = False) -> dict:
    """
    Shape one day for the time-grid: per person, timed blocks (tasks + meetings)
    positioned by clock time, untimed tasks, and capacity numbers.
    include_all=True shows EVERY person in the organigram (unbooked people appear
    as empty columns).
    """
    from scheduler import cap_for, _capacity_map
    caps = _capacity_map(organigram_df)
    events = events or []
    proj_keys = list(tasks_df["project"].dropna().unique())

    # tasks for this day
    tday = tasks_df[(tasks_df["due_date"] == day) &
                    (tasks_df["person"].astype(str).str.strip() != "")].copy()
    # meetings for this day, keyed by person
    mday = [e for e in events if e.get("date") == day and not e.get("all_day")]

    people = set(tday["person"]) | {e["person"] for e in mday}
    if include_all and organigram_df is not None and not organigram_df.empty:
        people |= set(organigram_df["person"].astype(str))
    people = sorted(p for p in people if str(p).strip())
    lo, hi = day_start_h * 60, day_end_h * 60

    cols = []
    for person in people:
        timed, untimed = [], []
        t_hours = m_hours = 0.0
        for _, t in tday[tday["person"] == person].iterrows():
            h = float(t["est_hours"]) if pd.notna(t["est_hours"]) else 0.0
            t_hours += h
            sdt, edt = t.get("start_dt"), t.get("end_dt")
            color = _stable_color(t["project"], proj_keys)
            if sdt is not None and edt is not None:
                s, e = _mins(sdt), _mins(edt)
                lo, hi = min(lo, s), max(hi, e)
                timed.append({"start": s, "end": e, "label": t["task"],
                              "sub": f'{t["project"]} · {h:.1f}h', "color": color, "kind": "task",
                              "title": f'{t["task"]} — {t["project"]} ({h:.1f}h, {t["status"]})'})
            else:
                untimed.append({"label": t["task"], "sub": f'{t["project"]} · {h:.0f}h',
                                "color": color})
        for e in [x for x in mday if x["person"] == person]:
            s, en = _mins(e["start"]), _mins(e["end"])
            lo, hi = min(lo, s), max(hi, en)
            m_hours += e["duration_hours"]
            atts = (", ".join(e["attendees"][:5]) if e.get("attendees") else "")
            timed.append({"start": s, "end": en, "label": e["title"],
                          "sub": f'{e["duration_hours"]:.1f}h meeting', "color": MEET_COLOR,
                          "kind": "meeting",
                          "title": f'📅 {e["title"]} ({e["duration_hours"]:.1f}h)'
                                   + (f' · {atts}' if atts else '')})
        cap = cap_for(person, caps)
        booked = t_hours + m_hours
        band = "red" if booked > cap else ("amber" if booked >= 0.85 * cap else "green")
        cols.append({"person": person, "timed": sorted(timed, key=lambda b: b["start"]),
                     "untimed": untimed, "task_hours": round(t_hours, 1),
                     "meeting_hours": round(m_hours, 1), "capacity": cap,
                     "band": band})
    # round window to whole hours
    lo = (lo // 60) * 60
    hi = -(-hi // 60) * 60
    return {"day": day, "people": cols, "axis_start": lo, "axis_end": hi}


def render_daily_grid_html(schedule: dict) -> str:
    """Google-Calendar-style HTML: time gutter + one column per person."""
    import html
    NAVY = "#0f172a"
    band_hex = {"green": "#16a34a", "amber": "#d97706", "red": "#dc2626"}
    lo, hi = schedule["axis_start"], schedule["axis_end"]
    span = max(60, hi - lo)
    PXH = 58
    grid_h = int(span / 60 * PXH)
    people = schedule["people"]
    if not people:
        return "<div style='padding:24px;color:#64748b'>No tasks or meetings scheduled for this day.</div>"

    # time gutter
    hours = list(range(lo // 60, hi // 60 + 1))
    gutter = "".join(
        f"<div style='height:{PXH}px;font-size:11px;color:#94a3b8;text-align:right;"
        f"padding-right:6px;border-top:1px solid #eef2f7'>{h:02d}:00</div>" for h in hours[:-1])

    cols_html = ""
    for c in people:
        b = band_hex[c["band"]]
        # untimed chips
        chips = "".join(
            f"<div title='{html.escape(u['sub'])}' style='background:{u['color']};color:#fff;"
            f"font-size:10px;padding:2px 6px;border-radius:5px;margin:2px 0;white-space:nowrap;"
            f"overflow:hidden;text-overflow:ellipsis'>{html.escape(u['label'])}</div>"
            for u in c["untimed"])
        untimed_html = (f"<div style='padding:4px;border-bottom:1px dashed #e2e8f0;"
                        f"min-height:8px'>{chips}</div>" if c["untimed"] else
                        "<div style='border-bottom:1px dashed #e2e8f0'></div>")
        # timed blocks
        blocks = ""
        for blk in c["timed"]:
            top = (blk["start"] - lo) / 60 * PXH
            h = max(16, (blk["end"] - blk["start"]) / 60 * PXH - 2)
            dashed = "border:1px dashed rgba(255,255,255,.6);" if blk["kind"] == "meeting" else ""
            blocks += (
                f"<div title='{html.escape(blk['title'])}' style='position:absolute;top:{top:.0f}px;"
                f"left:3px;right:3px;height:{h:.0f}px;background:{blk['color']};color:#fff;"
                f"border-radius:6px;padding:3px 6px;font-size:11px;overflow:hidden;{dashed}"
                f"box-shadow:0 1px 2px rgba(0,0,0,.15)'>"
                f"<b style='font-size:11px'>{html.escape(blk['label'][:40])}</b>"
                f"<div style='opacity:.85;font-size:10px'>{html.escape(blk['sub'])}</div></div>")
        # hour gridlines behind blocks
        lines = "".join(f"<div style='height:{PXH}px;border-top:1px solid #eef2f7'></div>"
                        for _ in hours[:-1])
        cols_html += (
            f"<div style='flex:1;min-width:150px;border-left:1px solid #eef2f7'>"
            f"<div style='padding:6px 8px;border-bottom:2px solid {b};position:sticky;top:0;"
            f"background:#fff;z-index:2'>"
            f"<div style='font-weight:700;font-size:13px;color:{NAVY}'>{html.escape(c['person'])}</div>"
            f"<div style='font-size:11px;color:{b};font-weight:600'>"
            f"{c['task_hours']}h task + {c['meeting_hours']}h mtg / {c['capacity']:.0f}h"
            f"{' ⚠' if c['band']=='red' else ''}</div></div>"
            f"{untimed_html}"
            f"<div style='position:relative;height:{grid_h}px'>{lines}{blocks}</div></div>")

    return (
        f"<div style='font-family:-apple-system,Helvetica,Arial,sans-serif;border:1px solid #e8eaed;"
        f"border-radius:12px;overflow:auto;max-height:760px'>"
        f"<div style='display:flex'>"
        f"<div style='width:54px;flex-shrink:0'>"
        f"<div style='padding:6px 8px;border-bottom:2px solid #e8eaed;height:34px'></div>"
        f"<div style='border-bottom:1px dashed #e2e8f0;min-height:8px'></div>{gutter}</div>"
        f"{cols_html}</div></div>")


def build_weekly_grid(tasks_df: pd.DataFrame, events: list, organigram_df: pd.DataFrame,
                      week_start: date, days: int = 5, include_all: bool = False) -> dict:
    """
    People (rows) x days (cols): task+meeting hours, band, item list per cell.
    include_all=True lists EVERY person in the organigram (so unbooked staff
    show as empty rows — useful for spotting who has free capacity).
    """
    from scheduler import cap_for, _capacity_map
    caps = _capacity_map(organigram_df)
    events = events or []
    day_list = [week_start + timedelta(days=i) for i in range(days)]

    # who appears this week
    wk_tasks = tasks_df[tasks_df["due_date"].apply(lambda d: isinstance(d, date) and d in day_list)]
    ppl = set(wk_tasks["person"].astype(str)) | {e["person"] for e in events if e.get("date") in day_list}
    if include_all and organigram_df is not None and not organigram_df.empty:
        ppl |= set(organigram_df["person"].astype(str))
    ppl = sorted(p for p in ppl if str(p).strip())

    cells = {}
    for p in ppl:
        for d in day_list:
            th = wk_tasks[(wk_tasks["person"] == p) & (wk_tasks["due_date"] == d)]["est_hours"].sum(min_count=1)
            th = float(th) if pd.notna(th) else 0.0
            mh = sum(e["duration_hours"] for e in events
                     if e["person"] == p and e.get("date") == d and not e.get("all_day"))
            cap = cap_for(p, caps)
            booked = th + mh
            band = "red" if booked > cap else ("amber" if booked >= 0.85 * cap else ("green" if booked else "none"))
            cells[(p, d)] = {"task_h": round(th, 1), "mtg_h": round(mh, 1),
                             "cap": cap, "band": band}
    return {"people": ppl, "days": day_list, "cells": cells}


def render_weekly_grid_html(grid: dict) -> str:
    """Scannable weekly resource table: rows = people, cols = days, traffic-lit cells."""
    import html
    band_bg = {"green": "#dcfce7", "amber": "#fef9c3", "red": "#fee2e2", "none": "#fff"}
    band_fg = {"green": "#166534", "amber": "#854d0e", "red": "#991b1b", "none": "#94a3b8"}
    days = grid["days"]
    if not grid["people"]:
        return "<div style='padding:24px;color:#64748b'>No commitments this week.</div>"
    head = "<th style='text-align:left;padding:8px 10px;position:sticky;left:0;background:#0f172a;color:#fff'>Resource</th>"
    head += "".join(f"<th style='padding:8px 10px;background:#0f172a;color:#fff;font-size:12px'>"
                    f"{d.strftime('%a')}<br><span style='font-weight:400;color:#cbd5e1'>{d.strftime('%d %b')}</span></th>"
                    for d in days)
    head += "<th style='padding:8px 10px;background:#0f172a;color:#fff'>Week</th>"

    rows = ""
    for p in grid["people"]:
        wk_total = 0.0
        tds = ""
        for d in days:
            c = grid["cells"][(p, d)]
            booked = c["task_h"] + c["mtg_h"]
            wk_total += booked
            txt = "—" if booked == 0 else f"{booked:.1f}h"
            sub = "" if booked == 0 else f"<div style='font-size:9px;opacity:.8'>{c['task_h']:.0f}t+{c['mtg_h']:.0f}m</div>"
            flag = " ⚠" if c["band"] == "red" else ""
            tds += (f"<td title='{p} · {d.strftime('%a %d %b')}: {c['task_h']}h tasks + {c['mtg_h']}h meetings / {c['cap']:.0f}h' "
                    f"style='text-align:center;padding:7px 8px;background:{band_bg[c['band']]};"
                    f"color:{band_fg[c['band']]};font-weight:700;font-size:13px;border:1px solid #fff'>"
                    f"{txt}{flag}{sub}</td>")
        rows += (f"<tr><td style='padding:7px 10px;font-weight:600;position:sticky;left:0;background:#f8fafc;"
                 f"border-right:1px solid #e2e8f0'>{html.escape(p)}</td>{tds}"
                 f"<td style='text-align:center;padding:7px 10px;font-weight:700;color:#0f172a'>{wk_total:.1f}h</td></tr>")

    return (f"<div style='font-family:-apple-system,Helvetica,Arial,sans-serif;border:1px solid #e8eaed;"
            f"border-radius:12px;overflow:auto;max-height:720px'>"
            f"<table style='border-collapse:collapse;width:100%'><thead><tr>{head}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


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
        "time_started": st.column_config.TextColumn("Start (hh:mm)"),
        "due_date": st.column_config.DateColumn("Date"),
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
