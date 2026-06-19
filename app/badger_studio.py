"""
Badger Studio — the T+W project-management soul, in one screen.

Wide-layout Streamlit app tying together the five engine modules:
  data_parser • scheduler • calendar_utils • report_generator • slack_sender

Run locally:
    cd badger
    streamlit run app/badger_studio.py
No Sheet configured? Admin > Upload CSV (data/sample_tasks.csv).

Phase 3 integration:
  - One load -> parse -> analyse, cached in session_state; shared by every page.
  - GLOBAL filters (date range / projects / person focus / active-only) cascade
    across all views.
  - Inline-editable task tables with write-back + confirmation + toast.
  - "Badger's Daily Ritual" — all briefs at once, ready to send.
  - Data-quality panel with a copyable fix list.
  - "Talk to Badger" — natural-language asks answered by the scheduler.

Design: clean, monochrome base with a navy/teal accent. Premium, minimal.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "utils"))
import data_parser as dp          # noqa: E402
import scheduler as sch           # noqa: E402
import calendar_utils as cal      # noqa: E402
import calendar_sync as cs        # noqa: E402
import report_generator as rg     # noqa: E402
import slack_sender as sl         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(HERE, "..", "reports")
SAMPLE_CSV = os.path.join(HERE, "..", "data", "sample_tasks.csv")
ORG_PATH = os.path.join(HERE, "..", "skills", "organigram_resources.md")

NAVY = "#0f172a"
TEAL = "#14b8a6"
BAND_HEX = {"green": "#16a34a", "amber": "#d97706", "red": "#dc2626", "none": "#9ca3af"}

st.set_page_config(layout="wide", page_title="Badger Studio — T+W", page_icon="✨")

# --------------------------------------------------------------------------- #
# Styling — navy/teal accent over a clean monochrome base
# --------------------------------------------------------------------------- #
st.markdown(f"""
<style>
.block-container {{padding-top: 2rem; max-width: 1500px;}}
[data-testid="stMetric"] {{background:#fff; border:1px solid #e8eaed;
  border-left:4px solid {TEAL}; border-radius:12px; padding:14px 16px;
  box-shadow:0 1px 2px rgba(15,23,42,0.04);}}
[data-testid="stMetricLabel"] {{color:#64748b; font-weight:600;}}
[data-testid="stMetricValue"] {{color:{NAVY};}}
h1,h2,h3 {{letter-spacing:-0.01em; color:{NAVY};}}
.stButton>button {{border-radius:10px; font-weight:600;}}
.badger-pill {{display:inline-block; padding:2px 10px; border-radius:999px;
  font-size:12px; font-weight:700; color:#fff;}}
.small {{color:#64748b; font-size:13px;}}
section[data-testid="stSidebar"] {{background:{NAVY};}}
section[data-testid="stSidebar"] * {{color:#e2e8f0;}}
section[data-testid="stSidebar"] .stButton>button {{background:{TEAL}; color:#04201c; border:none;}}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Secrets (safe)
# --------------------------------------------------------------------------- #
def gsheets_secrets():
    try:
        return dict(st.secrets["connections"]["gsheets"])
    except Exception:
        return None

def slack_secrets():
    try:
        return dict(st.secrets["slack"])
    except Exception:
        return None

def cal_secrets():
    """Calendar config from secrets, with an optional in-session override (Admin toggle)."""
    base = {}
    try:
        base = dict(st.secrets["google_calendar"])
    except Exception:
        base = {}
    ov = st.session_state.get("cal_override")
    if ov:
        base = {**base, **ov}
    return base or None


# --------------------------------------------------------------------------- #
# Load / analyse (cached in session_state)
# --------------------------------------------------------------------------- #
def load_data(csv_file=None):
    with st.spinner("Badger is reading the Sheet, balancing the team, and thinking…"):
        if csv_file is not None:
            bundle = dp.load_all(csv_file=csv_file)
        else:
            bundle = dp.load_all(secrets=gsheets_secrets())
        org = dp.load_organigram(ORG_PATH)
        analysis = sch.analyze(bundle.tasks_df, org, bundle.milestones_df)
        # Google Calendar (optional) — pull this week's meetings
        events, cal_err = [], None
        cfg = cs.load_calendar_config(cal_secrets())
        if cfg["enabled"]:
            events, cal_err = cs.get_events(
                cal_secrets(), organigram_df=org,
                time_min=datetime.combine(date.today(), datetime.min.time()),
                time_max=datetime.combine(date.today(), datetime.min.time()) + timedelta(days=8))
    st.session_state.bundle = bundle
    st.session_state.org = org
    st.session_state.analysis = analysis
    st.session_state.cal_events = events
    st.session_state.cal_err = cal_err
    st.session_state.loaded_at = datetime.now()
    return bundle


def ensure_loaded():
    if "bundle" in st.session_state:
        return
    try:
        load_data()
    except Exception as e:
        st.session_state.load_error = str(e)
        if os.path.exists(SAMPLE_CSV):
            load_data(csv_file=SAMPLE_CSV)


def pill(text, band):
    return f'<span class="badger-pill" style="background:{BAND_HEX.get(band,"#64748b")}">{text}</span>'


def _save_organigram(df: pd.DataFrame):
    lines = ["# T+W Organigram & Resource Directory for Badger", "",
             "**Purpose:** Living team directory.", "", "## Current T+W Team", ""]
    for _, r in df.iterrows():
        lines += [
            f"**Name:** {r.get('person','')}",
            f"**Role/Title:** {r.get('role','')}",
            f"**Core Skills:** {r.get('skills','')}",
            f"**Default Daily Productive Capacity:** {r.get('daily_capacity','7')}",
            f"**Slack Handle / User ID:** {r.get('slack','')}",
            f"**Email:** {r.get('email','')}", "",
        ]
    with open(ORG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# GLOBAL FILTERS — applied everywhere
# --------------------------------------------------------------------------- #
def apply_global(df: pd.DataFrame) -> pd.DataFrame:
    gf = st.session_state.get("gfilters", {})
    out = cal.apply_filters(
        df,
        persons=gf.get("persons") or None,
        projects=gf.get("projects") or None,
        date_range=gf.get("date_range"),
    )
    if gf.get("active_only"):
        out = out[out["status"].astype(str).str.lower() != "done"]
    return out


# --------------------------------------------------------------------------- #
# TALK TO BADGER — tiny natural-language intent router (scheduler-powered)
# --------------------------------------------------------------------------- #
def talk_to_badger(query: str, tasks_df, org, milestones_df) -> str:
    q = query.lower().strip()
    if not q:
        return ""
    names = list(org["person"]) if not org.empty else []
    named = next((n for n in names if n.split()[0].lower() in q or n.lower() in q), None)

    # focus a single person's load
    if named and any(w in q for w in ["load", "capacity", "busy", "overloaded", "focus", "rebalance"]):
        daily = sch.daily_utilization(tasks_df, org)
        d = daily[daily["person"] == named]
        if d.empty:
            return f"**{named}** has no allocated work in the current view. Plenty of room."
        lines = [f"**{named} — workload**"]
        for _, r in d.sort_values("date").iterrows():
            lines.append(f"- {r['date']}: {r['allocated_hours']:.0f}h / {r['capacity']:.0f}h "
                         f"= {r['utilization']:.0f}% ({r['band']})")
        sug = [s for s in sch.suggest_reallocations(tasks_df, org) if s["from_person"] == named]
        if sug:
            lines.append("\n**Badger suggests:**")
            for s in sug:
                lines.append(f"- Move *{s['task']}* ({s['hours']}h) to **{s['to_person']}** "
                             f"on {s['date']} — {s['reason']}")
        else:
            lines.append("\nNo clean moves needed right now.")
        return "\n".join(lines)

    if any(w in q for w in ["rebalance", "overallocat", "overloaded", "who is over", "who's over", "capacity"]):
        flags = sch.flag_overallocations(tasks_df, org)
        if not flags:
            return "No one is over capacity in the current view. Healthy week."
        out = ["**Overallocated right now:**"]
        for f in flags[:8]:
            out.append(f"- {f['person']} on {f['date']}: {f['utilization']:.0f}% "
                       f"({f['allocated_hours']}h/{f['capacity']}h)")
        sug = sch.suggest_reallocations(tasks_df, org)
        if sug:
            out.append("\n**Moves that help:**")
            for s in sug[:5]:
                out.append(f"- {s['task']} ({s['hours']}h): {s['from_person']} → {s['to_person']} "
                           f"on {s['date']}")
        return "\n".join(out)

    if "brief" in q and named:
        b = sch.build_daily_briefs(tasks_df, org).get(named)
        if not b:
            return f"{named} has nothing due today — no brief needed."
        return "```\n" + sl.generate_daily_brief(named, b["items"], b["total_hours"],
                                                 capacity=b["capacity"])["text"] + "\n```"

    if "milestone" in q:
        ups = sch.upcoming_milestones(milestones_df)
        if not ups:
            return "No milestones due in the next 7 days."
        return "**Upcoming milestones:**\n" + "\n".join(
            f"- {m['project']} · {m['milestone']} — {m['date']} ({m['days_out']}d)" for m in ups)

    if "due today" in q or "today" in q:
        t = tasks_df[tasks_df["due_date"] == date.today()]
        if t.empty:
            return "Nothing due today in the current view."
        return "**Due today:**\n" + "\n".join(
            f"- {r['person'] or 'Unassigned'}: {r['task']} ({r['project']})" for _, r in t.iterrows())

    return ("I can help with: *who's overloaded*, *rebalance [name]*, "
            "*[name]'s capacity*, *brief for [name]*, *upcoming milestones*, *due today*.")


# =========================================================================== #
# BOOT
# =========================================================================== #
ensure_loaded()
bundle = st.session_state.get("bundle")
org = st.session_state.get("org", pd.DataFrame())
analysis_full = st.session_state.get("analysis", {})

raw_tasks = bundle.tasks_df if bundle is not None else pd.DataFrame()
milestones_df = bundle.milestones_df if bundle is not None else pd.DataFrame()
meta = bundle.projects_meta if bundle is not None else {}

# --------------------------------------------------------------------------- #
# Sidebar — nav, source, global filters, talk-to-badger
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("## ✨ Badger Studio")
    st.caption("Powered by Badger • Badger is listening")
    page = st.radio("Navigate", [
        "Overview", "Calendar", "Resource Workload",
        "Projects & Tasks", "Reports", "Badger Comms", "Admin / Settings",
    ], label_visibility="collapsed")

    st.divider()
    src = getattr(bundle, "source", "—") if bundle else "—"
    st.markdown(f"**Source:** {'Google Sheet (live)' if src=='gsheets' else 'CSV (offline)' if src=='csv' else src}")
    if st.session_state.get("loaded_at"):
        st.caption("Loaded " + st.session_state.loaded_at.strftime("%H:%M:%S"))
    if st.button("🔄 Refresh data", use_container_width=True):
        try:
            load_data() if src != "csv" else load_data(csv_file=SAMPLE_CSV)
            st.toast("Refreshed from the Sheet.", icon="✅")
            st.rerun()
        except Exception as e:
            st.error(f"Refresh failed: {e}")

    # Global filters
    st.divider()
    st.markdown("**Global filters**")
    opts = cal.filter_options(raw_tasks) if not raw_tasks.empty else {"persons": [], "projects": []}
    gf_projects = st.multiselect("Projects", opts["projects"], key="gf_projects")
    gf_persons = st.multiselect("Person focus", opts["persons"], key="gf_persons")
    gf_active = st.checkbox("Active only (hide Done)", value=False, key="gf_active")
    use_dr = st.checkbox("Filter by date range", value=False, key="gf_use_dr")
    dr = None
    if use_dr:
        c1, c2 = st.columns(2)
        ds = c1.date_input("From", value=date.today())
        de = c2.date_input("To", value=date.today() + timedelta(days=14))
        dr = (ds, de)
    st.session_state.gfilters = {"projects": gf_projects, "persons": gf_persons,
                                 "active_only": gf_active, "date_range": dr}

# Apply global filters -> the working set every page uses
tasks_df = apply_global(raw_tasks)
analysis = sch.analyze(tasks_df, org, milestones_df) if not tasks_df.empty else analysis_full


# =========================================================================== #
# OVERVIEW
# =========================================================================== #
if page == "Overview":
    st.title("Overview")
    if st.session_state.get("load_error"):
        st.warning(f"Couldn't reach the Google Sheet — showing sample/last data.\n\n{st.session_state.load_error}")

    today = date.today()
    active_projects = len(tasks_df["project"].unique()) if not tasks_df.empty else 0
    daily_util = pd.DataFrame(analysis.get("daily_utilization", []))
    team_util = daily_util["utilization"].mean() if not daily_util.empty else 0
    upcoming = analysis.get("upcoming_milestones", [])
    due_today = int((tasks_df["due_date"] == today).sum()) if not tasks_df.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Projects", active_projects)
    c2.metric("Avg Utilisation", f"{team_util:.0f}%")
    c3.metric("Upcoming Milestones", len(upcoming))
    c4.metric("Tasks Due Today", due_today)

    st.divider()
    colL, colR = st.columns([3, 2])
    with colL:
        st.subheader("Project health")
        psum = bundle.project_summary_df if bundle is not None else pd.DataFrame()
        if not psum.empty and st.session_state.get("gfilters", {}).get("projects"):
            psum = psum[psum["project"].isin(st.session_state.gfilters["projects"])]
        if psum.empty:
            st.info("No tasks in view. Add tasks to your project tabs, then Refresh.")
        else:
            for _, r in psum.iterrows():
                st.markdown(
                    f"{pill(r['health'].upper(), r['health'])} **{r['project']}** "
                    f"<span class='small'>· {r['tasks']} tasks · {r['total_est_hours']}h "
                    f"· {r['tasks_overdue']} overdue · next: {r['next_milestone'] or '—'}</span>",
                    unsafe_allow_html=True)
    with colR:
        st.subheader("Badger's flags")
        flags = analysis.get("flags", [])
        if not flags:
            st.success("Nothing on fire. Capacity looks healthy.")
        for f in flags[:6]:
            st.markdown(
                f"{pill(f['severity'].upper(), f['severity'])} **{f['person']}** "
                f"<span class='small'>{f['date']} · {f['utilization']:.0f}% "
                f"({f['allocated_hours']}h/{f['capacity']}h)</span>", unsafe_allow_html=True)

    # Data quality
    anomalies = analysis.get("anomalies", [])
    issues = (bundle.issues if bundle is not None else []) + \
             [f"{a['type']} · {a['location']} — {a['detail']}" for a in anomalies]
    if issues:
        st.divider()
        st.subheader("⚠️ Data Quality Issues — here's what to fix in the Google Sheet")
        st.caption("Copy this list, fix in the Sheet, then Refresh.")
        st.code("\n".join(f"- {i}" for i in issues[:60]), language="text")

    st.divider()
    rc1, rc2 = st.columns([1, 1])
    if rc1.button("▶ Run full analysis", type="primary", use_container_width=True):
        load_data() if src != "csv" else load_data(csv_file=SAMPLE_CSV)
        st.toast("Analysis complete.", icon="✨"); st.rerun()
    if rc2.button("☀ Badger's Daily Ritual (all briefs)", use_container_width=True):
        st.session_state.show_ritual = True

    if st.session_state.get("show_ritual"):
        st.subheader("Badger's Daily Ritual")
        briefs = analysis.get("daily_briefs", {})
        if not briefs:
            st.info("No one has work due today — nothing to brief.")
        else:
            st.caption(f"{len(briefs)} briefs ready. Review, then send from Badger Comms.")
            cal_events = st.session_state.get("cal_events", [])
            for person, b in briefs.items():
                mline = cs.meetings_brief_line(cal_events, person, date.today()) if cal_events else None
                with st.expander(f"{person} — {b['total_hours']}h"
                                 + (" ⚠️ over" if b["over_capacity"] else "")):
                    st.code(sl.generate_daily_brief(person, b["items"], b["total_hours"],
                                                    context_from_projects=mline,
                                                    capacity=b["capacity"])["text"], language="markdown")


# =========================================================================== #
# STUDIO SCHEDULE — Daily / Weekly resource views + Classic calendar
# =========================================================================== #
elif page == "Calendar":
    import streamlit.components.v1 as components
    st.title("Studio Schedule")
    cal_events = st.session_state.get("cal_events", [])
    # respect the global person filter for the meeting overlay too
    gp = st.session_state.get("gfilters", {}).get("persons")
    if gp:
        cal_events = [e for e in cal_events if e.get("person") in gp]
    if cs.load_calendar_config(cal_secrets())["enabled"] and not cal_events and st.session_state.get("cal_err"):
        st.caption(f"📅 Calendar note: {st.session_state['cal_err']}")

    tabs = st.tabs(["🗓 Daily Resource View", "📊 Weekly Resource View", "Classic calendar"])

    # ---------- DAILY ----------
    with tabs[0]:
        h1, h2, h3 = st.columns([2, 2, 3])
        day = h1.date_input("Day", value=date.today(), key="daily_day")
        only_tasks = h2.checkbox("Only people with project tasks", value=False)
        st.caption("Project tasks coloured by project · meetings in grey (dashed). "
                   "Hover any block for detail. Capacity per person shown under their name.")
        sched = cal.build_daily_schedule(tasks_df, cal_events, org, day)
        if only_tasks:
            sched["people"] = [p for p in sched["people"] if p["task_hours"] > 0]
        components.html(cal.render_daily_grid_html(sched), height=780, scrolling=True)

        # editable strip for that day's tasks -> writes back to the Sheet
        tday = tasks_df[tasks_df["due_date"] == day]
        if not tday.empty:
            with st.expander(f"✏️ Edit {day.strftime('%a %d %b')} tasks (saves to Google Sheet)"):
                ed_src = cal.to_editor_frame(tday)
                ed = st.data_editor(ed_src, use_container_width=True, hide_index=True,
                                    column_config=cal.editor_column_config(), key="daily_editor")
                cc1, cc2 = st.columns([1, 3])
                conf = cc2.checkbox("Confirm save", key="daily_save_conf")
                if cc1.button("💾 Save day", key="daily_save"):
                    if bundle.source != "gsheets":
                        st.warning("Write-back needs the live Sheet.")
                    elif not conf:
                        st.warning("Tick confirm first.")
                    else:
                        res = dp.write_back_tasks(bundle.handles, ed, ed_src)
                        if res["errors"]:
                            st.error(" / ".join(res["errors"]))
                        else:
                            st.toast(f"Saved {res['updated']} cell(s).", icon="✅")
                            load_data(); st.rerun()

    # ---------- WEEKLY ----------
    with tabs[1]:
        default_mon = date.today() - timedelta(days=date.today().weekday())
        wk = st.date_input("Week starting (Mon)", value=default_mon, key="wk_start")
        wk = wk - timedelta(days=wk.weekday())  # snap to Monday
        st.caption("Green = healthy · amber = near capacity · red = over. "
                   "Each cell: total booked hours (tasks + meetings); hover for the split.")
        grid = cal.build_weekly_grid(tasks_df, cal_events, org, wk)
        components.html(cal.render_weekly_grid_html(grid), height=680, scrolling=True)

    # ---------- CLASSIC ----------
    with tabs[2]:
        color_by = st.selectbox("Colour by", ["project", "person", "status"], key="classic_color")
        events = cal.build_calendar_events(tasks_df, color_by=color_by)
        if cal_events:
            events = events + cs.calendar_overlay_events(cal_events)
        try:
            from streamlit_calendar import calendar as st_calendar
            st_calendar(events=events, options=cal.calendar_options(), key="badger_cal")
        except Exception:
            st.info("Calendar component unavailable — use the Daily/Weekly views above.")


# =========================================================================== #
# RESOURCE WORKLOAD
# =========================================================================== #
elif page == "Resource Workload":
    st.title("Resource Workload Studio")
    if tasks_df.empty:
        st.info("No tasks in view.")
    else:
        dates = [d for d in tasks_df["due_date"].dropna()]
        ds = min(dates) if dates else date.today()
        cc1, cc2 = st.columns(2)
        start = cc1.date_input("Week start", value=ds)
        end = cc2.date_input("Week end", value=start + timedelta(days=6))
        heat = cal.workload_heatmap(tasks_df, org, start=start, end=end)
        fig = cal.build_heatmap_figure(heat)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
        bars = cal.workload_bars(tasks_df, org, start=start, end=end)
        if not bars.empty:
            show = bars.copy(); show["utilization"] = show["utilization"].map(lambda u: f"{u:.0f}%")
            st.dataframe(show, use_container_width=True, hide_index=True)

        # Effective capacity for the chosen start day (meetings subtracted)
        cal_events = st.session_state.get("cal_events", [])
        if cal_events:
            st.markdown("**Effective capacity (meetings subtracted) — " + start.strftime("%a %d %b") + "**")
            eff = cs.workload_with_meetings(tasks_df, org, cal_events, start)
            if not eff.empty:
                ev = eff.copy(); ev["utilization"] = ev["utilization"].map(lambda u: f"{u:.0f}%")
                st.dataframe(ev, use_container_width=True, hide_index=True)
            else:
                st.caption("No task/meeting overlap on that day.")

        st.divider(); st.subheader("What-if reallocation")
        movable = tasks_df[tasks_df["person"].astype(str).str.strip() != ""]
        if not movable.empty and not org.empty:
            labels = {f'{r["task"]} · {r["person"]} ({r["_tab"]})': (r["_tab"], r["_row"])
                      for _, r in movable.iterrows()}
            w1, w2 = st.columns([3, 2])
            pick = w1.selectbox("Task to move", list(labels.keys()))
            newp = w2.selectbox("Reassign to", sorted(org["person"]))
            if st.button("Run what-if"):
                tab, row = labels[pick]
                wi = sch.what_if(tasks_df, org, [{"_tab": tab, "_row": row, "new_person": newp}])
                st.write(f"Red flags: **{wi['red_flags_before']} → {wi['red_flags_after']}** "
                         + ("✅ improves" if wi["improves"] else "no change"))
                if wi["after"]:
                    st.dataframe(pd.DataFrame(wi["after"]), use_container_width=True, hide_index=True)

        sug = analysis.get("reallocation_suggestions", [])
        if sug:
            st.subheader("Badger's suggested moves")
            for s in sug:
                st.markdown(f"- Move **{s['task']}** ({s['hours']}h) **{s['from_person']} → "
                            f"{s['to_person']}** on {s['date']} <span class='small'>· {s['reason']}</span>",
                            unsafe_allow_html=True)


# =========================================================================== #
# PROJECTS & TASKS
# =========================================================================== #
elif page == "Projects & Tasks":
    st.title("Projects & Tasks")
    if tasks_df.empty:
        st.info("No tasks in view.")
    else:
        search = st.text_input("🔎 Search tasks", "")
        fdf = tasks_df
        if search:
            fdf = fdf[fdf["task"].str.contains(search, case=False, na=False)]
        editor_df = cal.to_editor_frame(fdf)
        st.caption("Edit inline — status, %, person, hours, dates, notes — then Save.")
        edited = st.data_editor(editor_df, use_container_width=True, hide_index=True,
                                column_config=cal.editor_column_config(), key="task_editor")
        s1, s2 = st.columns([1, 3])
        confirm = s2.checkbox("Confirm: write these changes to the Google Sheet")
        if s1.button("💾 Save to Google Sheet", type="primary"):
            if bundle.source != "gsheets":
                st.warning("Write-back needs the live Sheet (you're in CSV mode).")
            elif not confirm:
                st.warning("Tick the confirm box first.")
            else:
                with st.spinner("Saving to the Sheet…"):
                    res = dp.write_back_tasks(bundle.handles, edited, editor_df)
                if res["errors"]:
                    st.error(" / ".join(res["errors"]))
                else:
                    st.toast(f"Saved {res['updated']} cell(s).", icon="✅")
                    load_data(); st.rerun()

        st.divider(); st.subheader("Client milestones document")
        proj = st.selectbox("Project", sorted(tasks_df["project"].unique()))
        if st.button("📄 Generate Client Milestones PDF"):
            os.makedirs(REPORTS_DIR, exist_ok=True)
            out = os.path.join(REPORTS_DIR, f"client_{proj.replace(' ','_')}.pdf")
            ms = [m for m in milestones_df.to_dict("records") if m["project"] == proj] \
                if not milestones_df.empty else None
            rg.generate_client_milestones_pdf(proj, raw_tasks, out, milestones=ms or None,
                                              meta=meta.get(proj, {}))
            with open(out, "rb") as fh:
                st.download_button("⬇ Download PDF", fh.read(),
                                   file_name=os.path.basename(out), mime="application/pdf")


# =========================================================================== #
# REPORTS
# =========================================================================== #
elif page == "Reports":
    st.title("Reports")
    t1, t2 = st.tabs(["Capacity report (internal)", "Client milestones"])
    with t1:
        r1, r2 = st.columns(2)
        start = r1.date_input("From", value=date.today())
        end = r2.date_input("To", value=date.today() + timedelta(days=14))
        if st.button("Generate capacity report", type="primary"):
            with st.spinner("Building capacity report…"):
                bars = cal.workload_bars(tasks_df, org, start=start, end=end)
                flags = sch.flag_overallocations(tasks_df, org)
                recs = sch.suggest_reallocations(tasks_df, org)
                os.makedirs(REPORTS_DIR, exist_ok=True)
                out = os.path.join(REPORTS_DIR, "capacity_report.pdf")
                rg.generate_capacity_report_pdf(start, end, bars, flags, recs, out)
            with open(out, "rb") as fh:
                st.download_button("⬇ Download capacity PDF", fh.read(),
                                   file_name="capacity_report.pdf", mime="application/pdf")
            st.markdown("**Preview**")
            st.markdown(rg.capacity_report_markdown(start, end, bars, flags, recs))
    with t2:
        if tasks_df.empty:
            st.info("No tasks in view.")
        else:
            proj = st.selectbox("Project", sorted(tasks_df["project"].unique()), key="rep_proj")
            if st.button("Generate client milestones", type="primary"):
                with st.spinner("Building client doc…"):
                    os.makedirs(REPORTS_DIR, exist_ok=True)
                    out = os.path.join(REPORTS_DIR, f"client_{proj.replace(' ','_')}.pdf")
                    ms = [m for m in milestones_df.to_dict("records") if m["project"] == proj] \
                        if not milestones_df.empty else None
                    rg.generate_client_milestones_pdf(proj, raw_tasks, out, milestones=ms or None,
                                                      meta=meta.get(proj, {}))
                with open(out, "rb") as fh:
                    st.download_button("⬇ Download client PDF", fh.read(),
                                       file_name=os.path.basename(out), mime="application/pdf")
                st.markdown("**Preview**")
                st.markdown(rg.client_milestones_markdown(proj, raw_tasks, meta=meta.get(proj, {})))


# =========================================================================== #
# BADGER COMMS
# =========================================================================== #
elif page == "Badger Comms":
    st.title("Badger Comms Center")
    briefs = analysis.get("daily_briefs", {})
    tabs = st.tabs(["Daily briefs", "Milestone reminders", "Custom message"])

    with tabs[0]:
        who = st.selectbox("Who", ["All Team"] + sorted(briefs.keys()))
        targets = list(briefs.keys()) if who == "All Team" else [who]
        if not targets:
            st.info("No one has work due today.")
        cal_events = st.session_state.get("cal_events", [])
        for person in targets:
            b = briefs[person]
            mline = cs.meetings_brief_line(cal_events, person, date.today()) if cal_events else None
            msg = sl.generate_daily_brief(person, b["items"], b["total_hours"],
                                          context_from_projects=mline, capacity=b["capacity"])
            with st.expander(f"{person} — {b['total_hours']}h" + (" ⚠️ over" if b["over_capacity"] else "")):
                st.code(msg["text"], language="markdown")
                handle = b.get("slack", "")
                c1, c2 = st.columns([1, 3])
                conf = c2.checkbox("Confirm send", key=f"cf_{person}")
                if c1.button("📤 Send", key=f"snd_{person}"):
                    if not handle:
                        st.warning("No Slack handle in organigram.")
                    elif not conf:
                        st.warning("Tick 'Confirm send' first.")
                    else:
                        res = sl.send_badger_message(handle, msg["text"], msg["blocks"], secrets=slack_secrets())
                        (st.toast("Sent.", icon="✅") if res["ok"] else st.error(res.get("error")))

    with tabs[1]:
        ups = analysis.get("upcoming_milestones", [])
        if not ups:
            st.info("No milestones due in the next 7 days.")
        for m in ups:
            who = m.get("responsibility") or "team"
            msg = sl.generate_milestone_reminder(who, m["milestone"], m["project"],
                                                 m["date"], days_out=m["days_out"])
            with st.expander(f"{m['project']} · {m['milestone']} — {m['date']} ({m['days_out']}d)"):
                st.code(msg["text"], language="markdown")

    with tabs[2]:
        target = st.text_input("Slack target (U… or #channel)")
        body = st.text_area("Message", "Quick one from Badger — ")
        c1, c2 = st.columns([1, 3])
        prev = c2.checkbox("Preview only", value=True)
        if c1.button("Send / Preview"):
            res = sl.send_badger_message(target, body, secrets=slack_secrets(), dry_run=prev)
            if res.get("preview"):
                st.info("Preview (not sent):"); st.code(body)
            elif res["ok"]:
                st.toast("Sent.", icon="✅")
            else:
                st.error(res.get("error"))


# =========================================================================== #
# ADMIN
# =========================================================================== #
elif page == "Admin / Settings":
    st.title("Admin / Settings")
    st.subheader("Connections")
    a1, a2 = st.columns(2)
    with a1:
        st.markdown("**Google Sheets**")
        if gsheets_secrets():
            st.success("Configured.")
            if bundle and bundle.source == "gsheets":
                st.caption(f"{len(meta)} project tabs · {len(raw_tasks)} tasks loaded.")
        else:
            st.warning("Not configured.")
    with a2:
        st.markdown("**Slack**")
        if st.button("Check Slack auth"):
            res = sl.test_connection(slack_secrets())
            (st.success(f"Connected as {res['bot']} in {res['team']}.") if res["ok"]
             else st.error(res.get("error")))

    # --- Test Badger Connection (sends a real hello) ---
    st.divider(); st.subheader("Google Calendar sync")
    _cfg = cs.load_calendar_config(cal_secrets())
    gc1, gc2 = st.columns([1, 2])
    with gc1:
        sync_on = st.checkbox("Sync Google Calendars", value=_cfg["enabled"],
                              help="Pull meetings and subtract them from capacity.")
        mode = st.selectbox("Mode", ["shared", "delegated"],
                            index=0 if _cfg["mode"] == "shared" else 1,
                            help="shared = calendars shared with the bot · delegated = domain-wide delegation")
    with gc2:
        default_cals = "\n".join(_cfg["calendars"]) or "\n".join(
            [e for e in org["email"] if str(e).strip()]) if not org.empty else ""
        cals_text = st.text_area("Calendars to watch (one email per line; blank = whole team)",
                                 value="\n".join(_cfg["calendars"]), height=90,
                                 placeholder="chris@tandw.co.za\njack@tandw.co.za")
    cals = [c.strip() for c in cals_text.splitlines() if c.strip()]
    st.session_state.cal_override = {"enabled": sync_on, "mode": mode, "calendars": cals}
    b1, b2 = st.columns([1, 3])
    if b1.button("Test calendar sync"):
        res = cs.test_connection(cal_secrets(), organigram_df=org)
        if res["ok"]:
            st.success(f"Connected — {res['events']} events in the next 24h.")
        else:
            st.error(res.get("error"))
    if b2.button("Apply & refresh"):
        load_data() if (bundle and bundle.source == "gsheets") else load_data(csv_file=SAMPLE_CSV)
        st.toast("Calendar settings applied.", icon="✅"); st.rerun()

    st.divider(); st.subheader("Test Badger Connection")
    st.caption("Send a real hello from Badger to yourself or a test channel — "
               "the end-to-end proof that Slack works.")
    cfg = sl.load_slack_config(slack_secrets())
    default_target = cfg.get("default_channel", "#badger-test")
    tc1, tc2 = st.columns([2, 1])
    target = tc1.text_input("Send to (your Slack user ID like U0…, or #channel)",
                            value=default_target,
                            help="Tip: DM works without inviting the bot. For a #channel, "
                                 "invite @Badger to it first (or add the chat:write.public scope).")
    if tc2.button("👋 Send hello", type="primary"):
        with st.spinner("Sending…"):
            res = sl.send_badger_message(
                target,
                "👋 Hello from Badger — your connection works. I'm listening and ready.",
                secrets=slack_secrets())
        if res["ok"]:
            st.toast("Hello sent — check Slack.", icon="✅")
            st.success(f"Sent to {res.get('channel', target)}.")
        else:
            st.error(f"Couldn't send: {res.get('error')}. "
                     "If 'channel_not_found', invite @Badger to that channel or DM a user ID instead.")

    st.divider()
    with st.expander("📖 Setup Wizard — connect Google Sheets & Slack (step by step)"):
        st.markdown("""
**Google Sheets (one-time, ~15 min)**

1. Go to **console.cloud.google.com** → top bar → *Select a project* → **New Project** → name it `Badger` → Create.
2. Search **"Google Sheets API"** → **Enable**. Repeat for **"Google Drive API"**.
3. **APIs & Services → Credentials → Create Credentials → Service account** → name `badger-bot` → Done.
4. Click the service account → **Keys → Add Key → Create new key → JSON** → it downloads a file. Keep it private.
5. Open the JSON, copy the **`client_email`** (ends in `…iam.gserviceaccount.com`). In your Master Tasks Sheet → **Share** → paste that email → **Editor** (or Viewer for read-only) → Send.
6. Put the key into Badger: copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and paste the JSON values into the `[connections.gsheets]` block, plus your Sheet URL. On Streamlit Cloud, paste the same into **Settings → Secrets**.

**Slack (one-time, ~10 min)**

1. **api.slack.com/apps → Create New App → From scratch** → name `Badger` → pick your workspace.
2. **OAuth & Permissions → Scopes → Bot Token Scopes** → add: `chat:write`, `chat:write.public`, `users:read`, `users:read.email`. (Add `channels:read` if you want Badger to list channels.)
3. **Install to Workspace → Allow** → copy the **Bot User OAuth Token** (`xoxb-…`).
4. Paste it into `secrets.toml` under `[slack] bot_token`, and set `default_channel`.
5. *(Optional)* **Basic Information → Display Information** → name it **Badger** and add an icon, so messages feel like they come from Badger.
6. Use **Test Badger Connection** above to confirm it works.

Full guide also lives in the project **README.md**.
""")

    st.divider(); st.subheader("Upload CSV (offline fallback)")
    up = st.file_uploader("Tasks CSV", type=["csv"])
    if up is not None and st.button("Load this CSV"):
        load_data(csv_file=up); st.toast("Loaded from CSV.", icon="✅"); st.rerun()

    st.divider(); st.subheader("Organigram (team & capacity)")
    if not org.empty:
        edited_org = st.data_editor(org, use_container_width=True, hide_index=True,
                                    num_rows="dynamic", key="org_editor")
        if st.button("💾 Save organigram"):
            _save_organigram(edited_org)
            st.toast("Saved.", icon="✅")
            load_data() if (bundle and bundle.source == "gsheets") else load_data(csv_file=SAMPLE_CSV)
            st.rerun()


# --------------------------------------------------------------------------- #
# TALK TO BADGER — available on every page (bottom)
# --------------------------------------------------------------------------- #
st.divider()
with st.expander("💬 Talk to Badger", expanded=False):
    st.caption("Ask in plain language: \"who's overloaded?\", \"rebalance Sarah\", "
               "\"brief for Kholiwe\", \"upcoming milestones\", \"due today\".")
    q = st.text_input("Ask Badger", key="talk_q",
                      placeholder="Badger, who's over capacity this week?")
    if q:
        st.markdown(talk_to_badger(q, tasks_df, org, milestones_df))
