"""
data_parser.py — Badger's bidirectional data layer.

Single Source of Truth: the Google Sheet "Badger's Project Management".
That sheet has ONE TAB PER PROJECT, each laid out as:

    Row 1   PROJECT NAME            | <name>
    Row 2   Creative Lead           | <name>
    Row 3   Project Lead            | <name>
    Row 4   Creative Director       | <name>
    Row 5   Client                  | <name>
    Row 6   Deliverables            | <text>
    Row 7   (blank)
    Row 8   KEY MILESTONES
    Row 9   Milestone Name | Date | Time | Responsibility
    Row 10+ <milestone rows ...>     (until a blank row)
    Row N   (blank)
    Row N+1 TASKS
    Row N+2 Task | Date | Time Started | Duration | Detail | Person
    Row N+3 <task rows ...>          (until end)

This module:
  - Connects via gspread (service account) OR reads an uploaded CSV.
  - Parses each project tab into a clean, canonical tasks DataFrame.
  - Returns DataFrames ready for st.data_editor.
  - Writes edits back to the EXACT tab + row + column in the Sheet.
  - Computes daily/weekly utilisation, grouped by person / date / project.
  - Merges with the organigram for capacity %.
  - Logs data-quality issues instead of crashing.

Canonical task schema (every loader converges to this):
    project, milestone, task, person, est_hours, start_date, due_date,
    status, pct_complete, notes, client_review,
    _tab, _row   (provenance for write-back; underscore = internal)

Built for Prompt 2.1 of the Badger Playbook. Principles from skills/main_brain.md:
Single Source of Truth, Resource-First, never crash on messy data — flag it gently.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

import pandas as pd
from dateutil import parser as dateparser

logger = logging.getLogger("badger.data_parser")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | badger | %(message)s")

# ----------------------------------------------------------------------------- #
# Canonical schema
# ----------------------------------------------------------------------------- #
TASK_COLUMNS = [
    "project", "milestone", "task", "person", "est_hours",
    "start_date", "due_date", "status", "pct_complete",
    "notes", "client_review", "_tab", "_row",
]

# Columns a human is allowed to edit in st.data_editor (mapped back to the Sheet)
EDITABLE_COLUMNS = [
    "task", "person", "est_hours", "start_date", "due_date",
    "status", "pct_complete", "notes",
]

VALID_STATUSES = ["Not Started", "In Progress", "Blocked", "Done", "On Hold"]

# Map canonical task fields -> the column header used in YOUR sheet's TASKS block.
# This is the contract between Badger's brain and the layout you actually built.
SHEET_TASK_HEADERS = {
    "task": "Task",
    "due_date": "Date",
    "start_date": "Time Started",   # your layout has "Time Started"; treated as start
    "est_hours": "Duration",        # duration of the task in hours
    "notes": "Detail",
    "person": "Person",
}


# ----------------------------------------------------------------------------- #
# Result container
# ----------------------------------------------------------------------------- #
@dataclass
class BadgerData:
    tasks_df: pd.DataFrame
    daily_workload_df: pd.DataFrame
    project_summary_df: pd.DataFrame
    person_capacity_df: pd.DataFrame
    milestones_df: pd.DataFrame
    projects_meta: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)        # data-quality flags
    source: str = "unknown"                            # "gsheets" or "csv"


# ----------------------------------------------------------------------------- #
# Small helpers
# ----------------------------------------------------------------------------- #
def parse_duration(value) -> Optional[float]:
    """Turn '2', '2h', '1.5 hrs', '90m', '90 min', '2:30' into hours (float)."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s == "" or s in {"-", "tbc", "tbd", "n/a"}:
        return None
    # h:mm form
    m = re.match(r"^(\d+):(\d{1,2})$", s)
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 60.0, 2)
    # combined "1h30min", "1h 30m", "2hr15min", "1h30"
    m = re.match(r"^(\d+)\s*(?:h|hr|hrs|hour|hours)\s*(\d{1,2})\s*(?:m|min|mins|minutes)?$", s)
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 60.0, 2)
    # minutes
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(m|min|mins|minutes)$", s)
    if m:
        return round(float(m.group(1)) / 60.0, 2)
    # hours (with or without unit) or bare number
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)?$", s)
    if m:
        return round(float(m.group(1)), 2)
    logger.warning("Could not parse duration %r -> leaving blank", value)
    return None


def parse_date(value) -> Optional[date]:
    """Lenient date parsing. Returns date or None (never raises)."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() in {"-", "tbc", "tbd", "n/a"}:
        return None
    try:
        return dateparser.parse(s, dayfirst=True).date()
    except (ValueError, OverflowError, TypeError):
        logger.warning("Could not parse date %r -> leaving blank", value)
        return None


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


# ----------------------------------------------------------------------------- #
# gspread connection
# ----------------------------------------------------------------------------- #
def get_gspread_client(secrets: Optional[dict] = None):
    """
    Build an authorised gspread client.

    `secrets` is the dict under [connections.gsheets] in secrets.toml (or
    st.secrets["connections"]["gsheets"]). If None, we try to read the local
    .streamlit/secrets.toml so the module is testable outside Streamlit.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    if secrets is None:
        secrets = _load_local_gsheets_secrets()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    info = {k: secrets[k] for k in (
        "type", "project_id", "private_key_id", "private_key",
        "client_email", "client_id", "auth_uri", "token_uri",
        "auth_provider_x509_cert_url", "client_x509_cert_url",
    )}
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds), secrets.get("spreadsheet")


def _load_local_gsheets_secrets() -> dict:
    """Read .streamlit/secrets.toml without needing Streamlit running."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", ".."))  # badger/
    path = os.path.join(root, ".streamlit", "secrets.toml")
    try:
        import tomllib  # py3.11+
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except ModuleNotFoundError:
        import tomli  # py3.10
        with open(path, "rb") as f:
            data = tomli.load(f)
    return data["connections"]["gsheets"]


# ----------------------------------------------------------------------------- #
# Parse ONE project tab (your block layout)
# ----------------------------------------------------------------------------- #
def _find_row(grid: list, marker: str) -> Optional[int]:
    """Return 0-based index of the first row whose first cell == marker."""
    for i, row in enumerate(grid):
        if row and _norm(row[0]).upper() == marker.upper():
            return i
    return None


def parse_project_tab(title: str, grid: list, issues: list):
    """
    grid = worksheet.get_all_values() (list of rows, each a list of strings).
    Returns (meta: dict, milestones: list[dict], tasks: list[dict]).
    """
    def cell(r, c=1):
        try:
            return _norm(grid[r][c])
        except IndexError:
            return ""

    meta = {
        "project": cell(0) or title,     # B1, fall back to tab name
        "creative_lead": cell(1),
        "project_lead": cell(2),
        "creative_director": cell(3),
        "client": cell(4),
        "deliverables": cell(5),
    }

    # --- Milestones ---
    milestones = []
    ms_head = _find_row(grid, "KEY MILESTONES")
    tasks_head = _find_row(grid, "TASKS")
    if ms_head is not None:
        # data starts 2 rows below the section header (header + column row)
        start = ms_head + 2
        end = tasks_head if tasks_head is not None else len(grid)
        for r in range(start, end):
            row = grid[r] if r < len(grid) else []
            name = _norm(row[0]) if row else ""
            if not name:
                continue
            milestones.append({
                "project": meta["project"],
                "milestone": name,
                "date": parse_date(row[1] if len(row) > 1 else ""),
                "time": _norm(row[2]) if len(row) > 2 else "",
                "responsibility": _norm(row[3]) if len(row) > 3 else "",
            })

    # --- Tasks ---
    tasks = []
    if tasks_head is None:
        issues.append(f"[{title}] No 'TASKS' section found — skipped tasks.")
        return meta, milestones, tasks

    header_row_idx = tasks_head + 1
    header = [_norm(h) for h in (grid[header_row_idx] if header_row_idx < len(grid) else [])]
    # Build a lookup: sheet header -> column index
    col_idx = {h: i for i, h in enumerate(header)}

    # Resolve which sheet column feeds each canonical field
    field_to_col = {}
    for canon, sheet_header in SHEET_TASK_HEADERS.items():
        if sheet_header in col_idx:
            field_to_col[canon] = col_idx[sheet_header]

    for r in range(header_row_idx + 1, len(grid)):
        row = grid[r] if r < len(grid) else []
        if not any(_norm(x) for x in row):
            continue  # skip fully blank rows

        def g(canon):
            ci = field_to_col.get(canon)
            return _norm(row[ci]) if ci is not None and ci < len(row) else ""

        task_name = g("task")
        if not task_name:
            continue  # a row with hours but no task name is noise

        tasks.append({
            "project": meta["project"],
            "milestone": "",  # your task block isn't milestone-linked; left blank
            "task": task_name,
            "person": g("person"),
            "est_hours": parse_duration(g("est_hours")),
            "start_date": parse_date(g("start_date")),
            "due_date": parse_date(g("due_date")),
            "status": "",          # not in your layout yet -> inferred later
            "pct_complete": 0,
            "notes": g("notes"),
            "client_review": "No",
            "_tab": title,
            "_row": r + 1,          # 1-based row number in the Sheet (for write-back)
        })

    return meta, milestones, tasks


# ----------------------------------------------------------------------------- #
# Loaders
# ----------------------------------------------------------------------------- #
def load_from_gsheets(secrets: Optional[dict] = None, spreadsheet_url: Optional[str] = None):
    """Read every project tab. Returns (tasks_df, milestones_df, projects_meta, issues, handles)."""
    issues = []
    client, url = get_gspread_client(secrets)
    url = spreadsheet_url or url
    sh = client.open_by_url(url)

    all_tasks, all_ms, meta = [], [], {}
    for ws in sh.worksheets():
        try:
            grid = ws.get_all_values()
            m, ms, tk = parse_project_tab(ws.title, grid, issues)
            meta[ws.title] = m
            all_ms.extend(ms)
            all_tasks.extend(tk)
        except Exception as e:                       # never let one bad tab kill the load
            issues.append(f"[{ws.title}] Failed to parse: {e}")
            logger.exception("Tab %s failed", ws.title)

    tasks_df = _frame(all_tasks, TASK_COLUMNS)
    ms_df = _frame(all_ms, ["project", "milestone", "date", "time", "responsibility"])
    handles = {"client": client, "spreadsheet": sh}
    return tasks_df, ms_df, meta, issues, handles


def load_from_csv(file_or_path):
    """
    Fallback loader. Expects a FLAT csv with canonical-ish headers:
    project, milestone, task, person, est_hours, start_date, due_date,
    status, pct_complete, notes, client_review
    Missing columns are tolerated.
    """
    issues = []
    raw = pd.read_csv(file_or_path, dtype=str).fillna("")
    raw.columns = [c.strip().lower().replace(" ", "_") for c in raw.columns]

    rows = []
    for i, r in raw.iterrows():
        rows.append({
            "project": _norm(r.get("project", "")),
            "milestone": _norm(r.get("milestone", "")),
            "task": _norm(r.get("task", "")),
            "person": _norm(r.get("person", r.get("assigned_person", ""))),
            "est_hours": parse_duration(r.get("est_hours", r.get("estimated_hours", ""))),
            "start_date": parse_date(r.get("start_date", "")),
            "due_date": parse_date(r.get("due_date", "")),
            "status": _norm(r.get("status", "")),
            "pct_complete": _to_int(r.get("pct_complete", r.get("%_complete", 0))),
            "notes": _norm(r.get("notes", r.get("dependencies_/_notes", ""))),
            "client_review": _norm(r.get("client_review", r.get("client_review_milestone?", "No"))) or "No",
            "_tab": _norm(r.get("project", "")),
            "_row": int(i) + 2,
        })
    tasks_df = _frame(rows, TASK_COLUMNS)
    ms_df = _frame([], ["project", "milestone", "date", "time", "responsibility"])
    return tasks_df, ms_df, {}, issues, {}


def _to_int(v, default=0):
    try:
        return int(float(str(v).replace("%", "").strip()))
    except (ValueError, TypeError):
        return default


def _frame(rows, cols):
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = pd.Series(dtype="object")
    return df[cols] if not df.empty else pd.DataFrame(columns=cols)


# ----------------------------------------------------------------------------- #
# Organigram (team capacity) parser
# ----------------------------------------------------------------------------- #
def load_organigram(path: Optional[str] = None) -> pd.DataFrame:
    """
    Parse skills/organigram_resources.md into a capacity DataFrame:
    person, role, skills, daily_capacity, slack, email.
    Lenient — designed for the markdown 'block per person' format.
    """
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, "..", ".."))
        path = os.path.join(root, "skills", "organigram_resources.md")

    people = []
    if not os.path.exists(path):
        logger.warning("Organigram not found at %s — capacity defaults to 7h.", path)
        return pd.DataFrame(columns=["person", "role", "skills", "daily_capacity", "slack", "email"])

    with open(path, encoding="utf-8") as f:
        text = f.read()

    # Parse line-by-line per block. Tolerant of messy markdown: handles
    # "**Label:** value", "**Label:value" (missing closing **), "Label: value",
    # extra spaces, etc. Never lets one field bleed into the next line.
    line_re = re.compile(r"^\s*\*{0,2}\s*([^:*]+?)\s*:\**\s*(.*?)\s*\**\s*$")
    blocks = re.split(r"\n\s*\n", text)
    for b in blocks:
        fields = {}
        for line in b.splitlines():
            m = line_re.match(line)
            if m:
                fields[m.group(1).strip().lower()] = m.group(2).strip()
        name = _norm(re.sub(r"\(.*?\)", "", fields.get("name", "")))
        if not name or name.startswith("["):
            continue  # skip placeholder / non-person blocks
        try:
            cap = float(re.findall(r"[\d.]+", fields.get("default daily productive capacity", "7"))[0])
        except (IndexError, ValueError):
            cap = 7.0
        people.append({
            "person": name,
            "role": fields.get("role/title", ""),
            "skills": fields.get("core skills", ""),
            "daily_capacity": cap,
            "slack": fields.get("slack handle / user id", ""),
            "email": fields.get("email", ""),
        })
    return pd.DataFrame(people)


# ----------------------------------------------------------------------------- #
# Enrichment + aggregation
# ----------------------------------------------------------------------------- #
def enrich_tasks(tasks_df: pd.DataFrame) -> pd.DataFrame:
    """Add derived columns the dashboard + scheduler rely on."""
    df = tasks_df.copy()
    today = date.today()

    def _week(d):
        return d.isocalendar()[1] if isinstance(d, date) else None

    df["due_date"] = df["due_date"].apply(lambda d: d if isinstance(d, date) else None)
    df["start_date"] = df["start_date"].apply(lambda d: d if isinstance(d, date) else None)
    df["week"] = df["due_date"].apply(_week)
    df["day"] = df["due_date"].apply(lambda d: d.strftime("%A") if isinstance(d, date) else "")
    df["is_client_review"] = df["client_review"].astype(str).str.strip().str.lower().isin(["yes", "y", "true"])
    df["is_overdue"] = df.apply(
        lambda r: isinstance(r["due_date"], date)
        and r["due_date"] < today
        and str(r["status"]).strip().lower() != "done",
        axis=1,
    )
    # Infer a status when the sheet doesn't carry one
    def _infer_status(r):
        s = _norm(r["status"])
        return s if s else "Not Started"
    df["status"] = df.apply(_infer_status, axis=1)
    df["est_hours"] = pd.to_numeric(df["est_hours"], errors="coerce")
    return df


def compute_daily_workload(tasks_df: pd.DataFrame) -> pd.DataFrame:
    """Hours allocated per person per day (uses due_date as the working day)."""
    df = tasks_df.dropna(subset=["due_date"]).copy()
    df = df[df["person"].astype(str).str.strip() != ""]
    if df.empty:
        return pd.DataFrame(columns=["person", "date", "allocated_hours", "task_count"])
    g = (df.groupby(["person", "due_date"])
           .agg(allocated_hours=("est_hours", "sum"),
                task_count=("task", "count"))
           .reset_index()
           .rename(columns={"due_date": "date"}))
    return g


def merge_person_capacity(daily_df: pd.DataFrame, organigram_df: pd.DataFrame) -> pd.DataFrame:
    """Per-person daily utilisation % vs capacity. Traffic-light band included."""
    if daily_df.empty:
        return pd.DataFrame(columns=["person", "date", "allocated_hours",
                                     "daily_capacity", "utilization", "band"])
    caps = organigram_df.set_index("person")["daily_capacity"].to_dict() if not organigram_df.empty else {}
    out = daily_df.copy()
    out["daily_capacity"] = out["person"].map(lambda p: caps.get(p, 7.0))
    out["utilization"] = (out["allocated_hours"] / out["daily_capacity"] * 100).round(1)

    def band(u):
        if u > 100:
            return "red"
        if u >= 85:
            return "amber"
        return "green"
    out["band"] = out["utilization"].apply(band)
    return out


def compute_project_summary(tasks_df: pd.DataFrame, milestones_df: pd.DataFrame,
                            meta: dict) -> pd.DataFrame:
    """One row per project: hours, task counts, next milestone, health."""
    rows = []
    for project in sorted(tasks_df["project"].dropna().unique()):
        pt = tasks_df[tasks_df["project"] == project]
        total = pd.to_numeric(pt["est_hours"], errors="coerce").sum()
        overdue = int(pt["is_overdue"].sum()) if "is_overdue" in pt else 0
        done = int((pt["status"].str.lower() == "done").sum())
        m = meta.get(project, {})
        pm = milestones_df[milestones_df["project"] == project] if not milestones_df.empty else pd.DataFrame()
        next_ms = ""
        if not pm.empty:
            future = pm.dropna(subset=["date"])
            future = future[future["date"] >= date.today()]
            if not future.empty:
                nr = future.sort_values("date").iloc[0]
                next_ms = f'{nr["milestone"]} ({nr["date"]})'
        health = "red" if overdue else ("green" if total else "amber")
        rows.append({
            "project": project,
            "client": m.get("client", ""),
            "project_lead": m.get("project_lead", ""),
            "total_est_hours": round(float(total), 1),
            "tasks": len(pt),
            "tasks_done": done,
            "tasks_overdue": overdue,
            "next_milestone": next_ms,
            "health": health,
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- #
# Data-quality report
# ----------------------------------------------------------------------------- #
def data_quality_report(tasks_df: pd.DataFrame, organigram_df: pd.DataFrame) -> list:
    """Human-readable list of things to fix in the Sheet (never blocks the app)."""
    issues = []
    known = set(organigram_df["person"]) if not organigram_df.empty else set()
    for _, r in tasks_df.iterrows():
        loc = f'{r["_tab"]} row {r["_row"]}'
        if not _norm(r["person"]):
            issues.append(f"{loc}: task '{r['task']}' has no assigned person.")
        elif known and r["person"] not in known:
            issues.append(f"{loc}: '{r['person']}' is not in the organigram (check spelling).")
        if pd.isna(r["est_hours"]):
            issues.append(f"{loc}: task '{r['task']}' has no/invalid Duration.")
        if r["due_date"] is None:
            issues.append(f"{loc}: task '{r['task']}' has no/invalid Date.")
    return issues


# ----------------------------------------------------------------------------- #
# WRITE-BACK — save edits to the exact Sheet cells
# ----------------------------------------------------------------------------- #
def write_back_tasks(handles: dict, edited_df: pd.DataFrame,
                     original_df: pd.DataFrame) -> dict:
    """
    Compare edited vs original and push only changed editable cells back to the
    Sheet, addressing each by its provenance (_tab + _row).

    Returns {"updated": n_cells, "errors": [...]}. Requires a gspread handle
    (i.e. the data must have come from gsheets, not CSV).
    """
    result = {"updated": 0, "errors": []}
    sh = handles.get("spreadsheet")
    if sh is None:
        result["errors"].append("No live Sheet connection — can't write back (CSV mode).")
        return result

    orig = original_df.set_index(["_tab", "_row"])
    batch_by_tab: dict = {}
    header_cache: dict = {}

    for _, row in edited_df.iterrows():
        key = (row["_tab"], row["_row"])
        if key not in orig.index:
            continue
        o = orig.loc[key]
        if row["_tab"] not in header_cache:
            try:
                header_cache[row["_tab"]] = _task_header_map(sh.worksheet(row["_tab"]))
            except Exception as e:
                result["errors"].append(f"Tab {row['_tab']} not found: {e}")
                header_cache[row["_tab"]] = {}
        header_map = header_cache[row["_tab"]]
        for canon in EDITABLE_COLUMNS:
            sheet_header = SHEET_TASK_HEADERS.get(canon)
            if not sheet_header or sheet_header not in header_map:
                continue
            new_val, old_val = row.get(canon), o.get(canon)
            if _changed(new_val, old_val):
                col = header_map[sheet_header] + 1  # 1-based
                a1 = _rowcol_to_a1(int(row["_row"]), col)
                batch_by_tab.setdefault(row["_tab"], []).append(
                    {"range": a1, "values": [[_to_cell(new_val)]]}
                )

    # one batch update per tab (keeps us well under the write quota)
    for tab, updates in batch_by_tab.items():
        try:
            sh.worksheet(tab).batch_update(updates)
            result["updated"] += len(updates)
        except Exception as e:
            result["errors"].append(f"Write to {tab} failed: {e}")
    return result


def _task_header_map(ws) -> dict:
    """Find the TASKS header row in a tab and map header -> 0-based col index."""
    grid = ws.get_all_values()
    th = _find_row(grid, "TASKS")
    if th is None or th + 1 >= len(grid):
        return {}
    header = [_norm(h) for h in grid[th + 1]]
    return {h: i for i, h in enumerate(header) if h}


def _changed(a, b) -> bool:
    a_na = a is None or (isinstance(a, float) and pd.isna(a))
    b_na = b is None or (isinstance(b, float) and pd.isna(b))
    if a_na and b_na:
        return False
    return _to_cell(a) != _to_cell(b)


def _to_cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _rowcol_to_a1(row: int, col: int) -> str:
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


# ----------------------------------------------------------------------------- #
# Orchestrator
# ----------------------------------------------------------------------------- #
def load_all(secrets: Optional[dict] = None, csv_file=None,
             organigram_path: Optional[str] = None) -> BadgerData:
    """
    One call to rule them all. Prefers the live Sheet; falls back to CSV.
    Returns a BadgerData bundle for the dashboard.
    """
    organigram_df = load_organigram(organigram_path)

    if csv_file is not None:
        tasks_df, ms_df, meta, issues, handles = load_from_csv(csv_file)
        source = "csv"
    else:
        tasks_df, ms_df, meta, issues, handles = load_from_gsheets(secrets)
        source = "gsheets"

    tasks_df = enrich_tasks(tasks_df)
    daily = compute_daily_workload(tasks_df)
    person_cap = merge_person_capacity(daily, organigram_df)
    proj_summary = compute_project_summary(tasks_df, ms_df, meta)
    issues = issues + data_quality_report(tasks_df, organigram_df)

    bundle = BadgerData(
        tasks_df=tasks_df,
        daily_workload_df=daily,
        project_summary_df=proj_summary,
        person_capacity_df=person_cap,
        milestones_df=ms_df,
        projects_meta=meta,
        issues=issues,
        source=source,
    )
    bundle.handles = handles  # type: ignore[attr-defined]
    return bundle


# ----------------------------------------------------------------------------- #
# Manual test:  python app/utils/data_parser.py
# ----------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("Badger data_parser self-test")
    try:
        data = load_all()
        print(f"Source: {data.source}")
        print(f"Projects: {list(data.projects_meta)}")
        print(f"Tasks: {len(data.tasks_df)} | Milestones: {len(data.milestones_df)}")
        print(f"People in organigram: {len(load_organigram())}")
        print("\nProject summary:")
        print(data.project_summary_df.to_string(index=False) if not data.project_summary_df.empty else "  (no tasks yet)")
        if data.issues:
            print(f"\nData-quality flags ({len(data.issues)}):")
            for i in data.issues[:20]:
                print("  -", i)
    except Exception as e:
        print("Self-test could not reach the Sheet (that's fine offline):", e)
        print("Falling back to CSV sample...")
        here = os.path.dirname(os.path.abspath(__file__))
        data = load_all(csv_file=os.path.join(here, "..", "..", "data", "sample_tasks.csv"))
        print(f"CSV tasks: {len(data.tasks_df)}")
        print(data.tasks_df[["project", "task", "person", "est_hours", "due_date"]].head().to_string(index=False))
