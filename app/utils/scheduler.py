"""
scheduler.py — Badger's brain for resource allocation and conflict detection.

Everything here is grounded in skills/main_brain.md:

  * Resource-First Scheduling  — people aren't interchangeable widgets; respect
    each person's Default Daily Productive Capacity (7h by default).
  * Proactive Clarity          — surface conflicts and milestone risk BEFORE they
    bite; suggest a fix, don't just complain.
  * Human-in-the-Loop          — Badger proposes ranked options; humans decide.
  * Priority Rules (main_brain) — 1) client commitments / reviews,
    2) overdue / critical path, 3) high-skill unique resources, 4) balance load.

Utilisation bands (main_brain "Workload Views"):
    green  < 85%      healthy
    amber  85–100%    watch — sustained load risks burnout
    red    > 100%     overallocated — needs action

Input: the clean DataFrames from data_parser (tasks_df, organigram, milestones).
Output: plain dicts/lists ready for the dashboard AND for Badger's Slack briefs,
so the logic is transparent and explainable to a human.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd

# Thresholds (from main_brain.md). Kept as constants so they're easy to tune.
AMBER_PCT = 85.0
RED_PCT = 100.0
DEFAULT_CAPACITY = 7.0
WORKING_DAYS_PER_WEEK = 5
MILESTONE_LOOKAHEAD_DAYS = 7      # "within 5-7 days" -> heads-up window


# --------------------------------------------------------------------------- #
# Capacity helpers
# --------------------------------------------------------------------------- #
def _capacity_map(organigram_df: pd.DataFrame) -> dict:
    """person -> daily capacity (hours). Unknown people default to 7h."""
    if organigram_df is None or organigram_df.empty:
        return {}
    return organigram_df.set_index("person")["daily_capacity"].to_dict()


def _skills_map(organigram_df: pd.DataFrame) -> dict:
    """person -> (role + skills) lowercased token set, for reallocation matching."""
    out = {}
    if organigram_df is None or organigram_df.empty:
        return out
    for _, r in organigram_df.iterrows():
        text = f'{r.get("role","")} {r.get("skills","")}'.lower()
        tokens = {t.strip(" ,/.") for t in text.replace(",", " ").split() if len(t) > 2}
        out[r["person"]] = tokens
    return out


def cap_for(person: str, caps: dict) -> float:
    return float(caps.get(person, DEFAULT_CAPACITY))


def _band(util: float) -> str:
    if util > RED_PCT:
        return "red"
    if util >= AMBER_PCT:
        return "amber"
    return "green"


# --------------------------------------------------------------------------- #
# Utilisation
# --------------------------------------------------------------------------- #
def daily_utilization(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per person, per day: hours allocated, capacity, utilisation %, band.
    Uses due_date as the working day (that's the field your sheet carries).
    """
    caps = _capacity_map(organigram_df)
    df = tasks_df.dropna(subset=["due_date"]).copy()
    df = df[df["person"].astype(str).str.strip() != ""]
    if df.empty:
        return pd.DataFrame(columns=["person", "date", "allocated_hours",
                                     "capacity", "utilization", "band"])
    g = (df.groupby(["person", "due_date"])
           .agg(allocated_hours=("est_hours", "sum"))
           .reset_index()
           .rename(columns={"due_date": "date"}))
    g["capacity"] = g["person"].map(lambda p: cap_for(p, caps))
    g["utilization"] = (g["allocated_hours"] / g["capacity"] * 100).round(1)
    g["band"] = g["utilization"].apply(_band)
    return g


def weekly_utilization(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per person, per ISO week: hours vs weekly capacity (daily cap x 5 days).
    Gives the 'sustained load' picture main_brain asks us to watch.
    """
    caps = _capacity_map(organigram_df)
    df = tasks_df.dropna(subset=["due_date"]).copy()
    df = df[df["person"].astype(str).str.strip() != ""]
    if df.empty:
        return pd.DataFrame(columns=["person", "year", "week", "allocated_hours",
                                     "weekly_capacity", "utilization", "band"])
    df["year"] = df["due_date"].apply(lambda d: d.isocalendar()[0])
    df["week"] = df["due_date"].apply(lambda d: d.isocalendar()[1])
    g = (df.groupby(["person", "year", "week"])
           .agg(allocated_hours=("est_hours", "sum"))
           .reset_index())
    g["weekly_capacity"] = g["person"].map(lambda p: cap_for(p, caps) * WORKING_DAYS_PER_WEEK)
    g["utilization"] = (g["allocated_hours"] / g["weekly_capacity"] * 100).round(1)
    g["band"] = g["utilization"].apply(_band)
    return g


# --------------------------------------------------------------------------- #
# Overallocation flags
# --------------------------------------------------------------------------- #
def flag_overallocations(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                         include_amber: bool = True) -> list:
    """
    Return a list of overallocation flags, each naming the person, the date,
    the numbers, and the exact tasks causing it — so Badger can explain WHY.

    Sorted worst-first (red before amber, highest utilisation first).
    """
    daily = daily_utilization(tasks_df, organigram_df)
    if daily.empty:
        return []
    bad = daily[daily["band"] == "red"]
    if include_amber:
        bad = daily[daily["band"].isin(["red", "amber"])]

    flags = []
    for _, row in bad.iterrows():
        # the tasks on this person/day, heaviest first
        culprits = tasks_df[(tasks_df["person"] == row["person"]) &
                            (tasks_df["due_date"] == row["date"])]
        culprits = culprits.sort_values("est_hours", ascending=False)
        flags.append({
            "person": row["person"],
            "date": row["date"],
            "allocated_hours": round(float(row["allocated_hours"]), 1),
            "capacity": float(row["capacity"]),
            "utilization": float(row["utilization"]),
            "severity": row["band"],
            "tasks": [
                {"task": t["task"], "project": t["project"],
                 "hours": float(t["est_hours"]) if pd.notna(t["est_hours"]) else None,
                 "_tab": t["_tab"], "_row": int(t["_row"])}
                for _, t in culprits.iterrows()
            ],
        })
    # worst first
    sev_rank = {"red": 0, "amber": 1}
    flags.sort(key=lambda f: (sev_rank[f["severity"]], -f["utilization"]))
    return flags


# --------------------------------------------------------------------------- #
# Reallocation suggestions
# --------------------------------------------------------------------------- #
def _skill_overlap(a: set, b: set) -> int:
    return len(a & b)


def suggest_reallocations(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                          max_suggestions: int = 10) -> list:
    """
    For each overallocated person/day, propose moving the lightest movable task
    to the best-fit teammate: someone with matching role/skills AND spare
    capacity on that day. Ranked by least disruption (main_brain conflict rule).

    We move the SMALLEST task first — smallest change that relieves the day.
    Each suggestion shows projected utilisation for both people so a human can
    decide with eyes open.
    """
    caps = _capacity_map(organigram_df)
    skills = _skills_map(organigram_df)
    daily = daily_utilization(tasks_df, organigram_df)
    if daily.empty:
        return []

    # current load per person/day (for checking candidate spare capacity)
    load = daily.set_index(["person", "date"])["allocated_hours"].to_dict()
    over = daily[daily["band"] == "red"].sort_values("utilization", ascending=False)

    suggestions = []
    for _, row in over.iterrows():
        person, day = row["person"], row["date"]
        # tasks for this overloaded day, smallest first (least disruption to move)
        day_tasks = tasks_df[(tasks_df["person"] == person) &
                             (tasks_df["due_date"] == day)].copy()
        day_tasks = day_tasks.sort_values("est_hours", ascending=True)
        from_skills = skills.get(person, set())

        for _, task in day_tasks.iterrows():
            t_hours = float(task["est_hours"]) if pd.notna(task["est_hours"]) else 0.0
            if t_hours <= 0:
                continue
            # find candidate teammates
            candidates = []
            for cand in (organigram_df["person"] if organigram_df is not None
                         and not organigram_df.empty else []):
                if cand == person:
                    continue
                cand_cap = cap_for(cand, caps)
                cand_load = float(load.get((cand, day), 0.0))
                projected = cand_load + t_hours
                if projected > cand_cap:
                    continue  # would just move the problem — skip
                overlap = _skill_overlap(from_skills, skills.get(cand, set()))
                candidates.append({
                    "to_person": cand,
                    "skill_overlap": overlap,
                    "projected_load": round(projected, 1),
                    "projected_util": round(projected / cand_cap * 100, 1),
                })
            if not candidates:
                continue
            # rank: best skill match first, then lowest resulting utilisation
            candidates.sort(key=lambda c: (-c["skill_overlap"], c["projected_util"]))
            best = candidates[0]

            new_from = round((row["allocated_hours"] - t_hours) / row["capacity"] * 100, 1)
            suggestions.append({
                "from_person": person,
                "date": day,
                "task": task["task"],
                "project": task["project"],
                "hours": t_hours,
                "to_person": best["to_person"],
                "reason": (f'{best["to_person"]} has capacity '
                           f'({best["projected_util"]}% after) '
                           + (f'and {best["skill_overlap"]} matching skill(s)'
                              if best["skill_overlap"] else 'and a relevant role')),
                "from_util_before": float(row["utilization"]),
                "from_util_after": new_from,
                "to_util_after": best["projected_util"],
                "_tab": task["_tab"],
                "_row": int(task["_row"]),
                "alternatives": candidates[1:3],   # a couple of backups
            })
            break  # one suggestion per overloaded day is enough to relieve it

    return suggestions[:max_suggestions]


# --------------------------------------------------------------------------- #
# What-if scenarios
# --------------------------------------------------------------------------- #
def what_if(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
            changes: list) -> dict:
    """
    Model proposed reassignments WITHOUT touching the Sheet.

    `changes` = [{"_tab": ..., "_row": ..., "new_person": ...}, ...]
    Returns before/after daily utilisation for everyone affected, plus whether
    the change clears the red flags. Pure simulation — Human-in-the-Loop.
    """
    before = daily_utilization(tasks_df, organigram_df)
    sim = tasks_df.copy()
    affected = set()
    for ch in changes:
        mask = (sim["_tab"] == ch["_tab"]) & (sim["_row"] == ch["_row"])
        if mask.any():
            affected.add(sim.loc[mask, "person"].iloc[0])
            affected.add(ch["new_person"])
            sim.loc[mask, "person"] = ch["new_person"]
    after = daily_utilization(sim, organigram_df)

    def _slice(df):
        return df[df["person"].isin(affected)].sort_values(["person", "date"])

    red_before = int((before["band"] == "red").sum())
    red_after = int((after["band"] == "red").sum())
    return {
        "affected_people": sorted(affected),
        "before": _slice(before).to_dict("records"),
        "after": _slice(after).to_dict("records"),
        "red_flags_before": red_before,
        "red_flags_after": red_after,
        "improves": red_after < red_before,
    }


# --------------------------------------------------------------------------- #
# Upcoming milestones
# --------------------------------------------------------------------------- #
def upcoming_milestones(milestones_df: pd.DataFrame, as_of: Optional[date] = None,
                        days: int = MILESTONE_LOOKAHEAD_DAYS) -> list:
    """Milestones due within `days`, sorted soonest-first, with days-out count."""
    as_of = as_of or date.today()
    horizon = as_of + timedelta(days=days)
    if milestones_df is None or milestones_df.empty:
        return []
    df = milestones_df.dropna(subset=["date"]).copy()
    df = df[(df["date"] >= as_of) & (df["date"] <= horizon)]
    df = df.sort_values("date")
    return [{
        "project": r["project"],
        "milestone": r["milestone"],
        "date": r["date"],
        "days_out": (r["date"] - as_of).days,
        "time": r.get("time", ""),
        "responsibility": r.get("responsibility", ""),
    } for _, r in df.iterrows()]


# --------------------------------------------------------------------------- #
# Anomaly detection
# --------------------------------------------------------------------------- #
def detect_anomalies(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                     as_of: Optional[date] = None) -> list:
    """
    Spot the things main_brain tells us to catch early:
      - tasks with no assignee
      - past-due tasks not marked Done
      - unrealistic hours (single task > one person's daily capacity)
      - assignee not in the organigram (likely a typo)
    Returns a list of {type, severity, location, detail}.
    """
    as_of = as_of or date.today()
    caps = _capacity_map(organigram_df)
    known = set(organigram_df["person"]) if organigram_df is not None and not organigram_df.empty else set()
    out = []
    for _, r in tasks_df.iterrows():
        loc = f'{r["_tab"]} · "{r["task"]}"'
        person = str(r["person"]).strip()
        if not person:
            out.append({"type": "no_assignee", "severity": "high",
                        "location": loc, "detail": "Task has no assigned person."})
        elif known and person not in known:
            out.append({"type": "unknown_person", "severity": "medium",
                        "location": loc,
                        "detail": f"'{person}' isn't in the organigram — check spelling."})
        if isinstance(r["due_date"], date) and r["due_date"] < as_of \
                and str(r["status"]).lower() != "done":
            out.append({"type": "overdue", "severity": "high", "location": loc,
                        "detail": f'Past due ({r["due_date"]}) and not Done.'})
        if pd.notna(r["est_hours"]) and person:
            if float(r["est_hours"]) > cap_for(person, caps):
                out.append({"type": "unrealistic_hours", "severity": "medium",
                            "location": loc,
                            "detail": (f'{r["est_hours"]}h on one day exceeds '
                                       f'{person}\'s {cap_for(person, caps)}h capacity.')})
    return out


# --------------------------------------------------------------------------- #
# Daily briefs (structured input for Badger's Slack messages)
# --------------------------------------------------------------------------- #
def build_daily_briefs(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
                       brief_date: Optional[date] = None) -> dict:
    """
    For each person with work due on `brief_date`, build a prioritised brief.

    Priority order follows main_brain.md:
        1) client-review tasks
        2) overdue tasks
        3) larger tasks (heavier lift first)

    Returns {person: {date, total_hours, capacity, utilization, over_capacity,
                      slack, items:[{task, project, hours, why, _tab, _row}]}}.
    The slack_sender (Prompt 2.5) turns this into Badger's actual message.
    """
    brief_date = brief_date or date.today()
    caps = _capacity_map(organigram_df)
    slack = (organigram_df.set_index("person")["slack"].to_dict()
             if organigram_df is not None and not organigram_df.empty else {})

    todays = tasks_df[(tasks_df["due_date"] == brief_date) &
                      (tasks_df["person"].astype(str).str.strip() != "")].copy()
    # also pull overdue-and-not-done so people don't lose track of slippage
    overdue = tasks_df[(tasks_df["is_overdue"]) &
                       (tasks_df["person"].astype(str).str.strip() != "")].copy()
    pool = pd.concat([todays, overdue]).drop_duplicates(subset=["_tab", "_row"])

    briefs = {}
    for person, grp in pool.groupby("person"):
        def _priority(t):
            return (
                0 if t["is_client_review"] else 1,
                0 if t["is_overdue"] else 1,
                -(float(t["est_hours"]) if pd.notna(t["est_hours"]) else 0),
            )
        items_sorted = sorted([r for _, r in grp.iterrows()], key=_priority)
        total = sum(float(t["est_hours"]) for t in items_sorted if pd.notna(t["est_hours"]))
        capacity = cap_for(person, caps)
        items = []
        for t in items_sorted:
            why = []
            if t["is_client_review"]:
                why.append("client review")
            if t["is_overdue"]:
                why.append(f'overdue ({t["due_date"]})')
            items.append({
                "task": t["task"],
                "project": t["project"],
                "hours": float(t["est_hours"]) if pd.notna(t["est_hours"]) else None,
                "why": ", ".join(why),
                "_tab": t["_tab"],
                "_row": int(t["_row"]),
            })
        briefs[person] = {
            "date": brief_date,
            "total_hours": round(total, 1),
            "capacity": capacity,
            "utilization": round(total / capacity * 100, 1) if capacity else None,
            "over_capacity": total > capacity,
            "slack": slack.get(person, ""),
            "items": items,
        }
    return briefs


# --------------------------------------------------------------------------- #
# Orchestrator — one call the dashboard uses
# --------------------------------------------------------------------------- #
def analyze(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame,
            milestones_df: Optional[pd.DataFrame] = None,
            as_of: Optional[date] = None) -> dict:
    """Run the full analysis and return one structured dict for the dashboard + Slack."""
    as_of = as_of or date.today()
    if milestones_df is None:
        milestones_df = pd.DataFrame(columns=["project", "milestone", "date", "time", "responsibility"])
    return {
        "as_of": as_of,
        "daily_utilization": daily_utilization(tasks_df, organigram_df).to_dict("records"),
        "weekly_utilization": weekly_utilization(tasks_df, organigram_df).to_dict("records"),
        "flags": flag_overallocations(tasks_df, organigram_df),
        "reallocation_suggestions": suggest_reallocations(tasks_df, organigram_df),
        "upcoming_milestones": upcoming_milestones(milestones_df, as_of),
        "anomalies": detect_anomalies(tasks_df, organigram_df, as_of),
        "daily_briefs": build_daily_briefs(tasks_df, organigram_df, as_of),
    }


def analyze_bundle(bundle, as_of: Optional[date] = None) -> dict:
    """Convenience: run analyze() straight off a data_parser.BadgerData bundle."""
    import data_parser as dp
    organigram_df = dp.load_organigram()
    return analyze(bundle.tasks_df, organigram_df, bundle.milestones_df, as_of)


# --------------------------------------------------------------------------- #
# Manual test:  python app/utils/scheduler.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(__file__))
    import data_parser as dp

    here = os.path.dirname(os.path.abspath(__file__))
    csv = os.path.join(here, "..", "..", "data", "sample_tasks.csv")
    data = dp.load_all(csv_file=csv)
    org = dp.load_organigram()

    print("=== Badger scheduler self-test (sample data) ===")
    result = analyze(data.tasks_df, org, data.milestones_df)

    print(f"\nOverallocation flags: {len(result['flags'])}")
    for f in result["flags"][:5]:
        print(f"  [{f['severity'].upper()}] {f['person']} {f['date']}: "
              f"{f['allocated_hours']}h / {f['capacity']}h = {f['utilization']}%")
        for t in f["tasks"]:
            print(f"        - {t['task']} ({t['hours']}h, {t['project']})")

    print(f"\nReallocation suggestions: {len(result['reallocation_suggestions'])}")
    for s in result["reallocation_suggestions"][:5]:
        print(f"  Move '{s['task']}' ({s['hours']}h) {s['from_person']} -> {s['to_person']} "
              f"on {s['date']}")
        print(f"        {s['from_person']}: {s['from_util_before']}% -> {s['from_util_after']}% | "
              f"{s['to_person']} -> {s['to_util_after']}%")
        print(f"        why: {s['reason']}")

    print(f"\nAnomalies: {len(result['anomalies'])}")
    for a in result["anomalies"][:6]:
        print(f"  [{a['severity']}] {a['type']}: {a['location']} — {a['detail']}")

    print(f"\nDaily briefs for {result['as_of']}: {len(result['daily_briefs'])} people")
    for person, b in list(result["daily_briefs"].items())[:5]:
        print(f"  {person}: {b['total_hours']}h / {b['capacity']}h "
              f"({'OVER' if b['over_capacity'] else 'ok'})")
        for it in b["items"][:4]:
            tag = f" [{it['why']}]" if it["why"] else ""
            print(f"        - {it['task']} ({it['hours']}h){tag}")

    # what-if demo: move the first reallocation suggestion and see if reds drop
    if result["reallocation_suggestions"]:
        s = result["reallocation_suggestions"][0]
        wi = what_if(data.tasks_df, org,
                     [{"_tab": s["_tab"], "_row": s["_row"], "new_person": s["to_person"]}])
        print(f"\nWhat-if (apply first suggestion): red flags "
              f"{wi['red_flags_before']} -> {wi['red_flags_after']} "
              f"({'improves' if wi['improves'] else 'no change'})")
