"""
report_generator.py - Badger's document studio.

Two flagship outputs, plus quick Markdown/HTML export of each:

  1. generate_client_milestones_pdf(project, tasks_df, output_path, ...)
     Client-facing. Calm, confidence-inspiring timeline of milestones,
     deliverables, the moments we'll bring the client in, and what we need
     from them. Signed by Badger on behalf of T+W.

  2. generate_capacity_report_pdf(start, end, utilization_data, flags,
     recommendations, output_path)
     Internal only. Team utilisation (table + native bar chart), who's over /
     under, bottleneck risks, recommended actions, and Badger's direct take.

Design follows T+W house style: monochrome, typography-led, generous space,
every element earning its place. Bars are drawn natively with fpdf2 rectangles,
so there's no matplotlib dependency. Fonts use Helvetica (clean, universal);
T+W's Anonymous Pro can be dropped in later via add_font if desired.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional

import pandas as pd
from fpdf import FPDF

# Monochrome palette + restrained status accents
BLACK = (17, 17, 17)
GREY = (110, 110, 110)
LIGHT = (235, 235, 235)
HAIR = (200, 200, 200)
GREEN = (22, 163, 74)
AMBER = (217, 119, 6)
RED = (220, 38, 38)
BAND_RGB = {"green": GREEN, "amber": AMBER, "red": RED, "none": HAIR}

PAGE_W = 210  # A4 mm
MARGIN = 18
CONTENT_W = PAGE_W - 2 * MARGIN


def _fmt_date(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%d %b %Y")
    return str(d) if d else "TBC"


# --------------------------------------------------------------------------- #
# Base PDF - shared T+W chrome (header band, footer with Badger signature)
# --------------------------------------------------------------------------- #
class BadgerPDF(FPDF):
    doc_kicker = ""     # small label top-right, e.g. "CLIENT UPDATE"
    doc_confidential = False

    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(*BLACK)
        self.set_xy(MARGIN, 12)
        self.cell(40, 8, "T+W")
        if self.doc_kicker:
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*GREY)
            self.set_xy(PAGE_W - MARGIN - 60, 14)
            self.cell(60, 5, self.doc_kicker, align="R")
        # hairline rule
        self.set_draw_color(*HAIR)
        self.set_line_width(0.3)
        self.line(MARGIN, 24, PAGE_W - MARGIN, 24)
        self.set_y(32)

    def footer(self):
        self.set_y(-15)
        self.set_draw_color(*HAIR)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*GREY)
        left = "T+W - tell great stories"
        if self.doc_confidential:
            left = "T+W - Internal & Confidential"
        self.set_xy(MARGIN, self.get_y() + 1)
        self.cell(0, 6, left)
        self.set_xy(PAGE_W - MARGIN - 40, self.get_y())
        self.cell(40, 6, f"Page {self.page_no()}", align="R")

    # --- building blocks ---------------------------------------------------- #
    def h1(self, text):
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(*BLACK)
        self.multi_cell(CONTENT_W, 9, text)
        self.ln(2)

    def h2(self, text):
        self.ln(3)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*BLACK)
        self.cell(0, 7, text.upper())
        self.ln(8)
        self.set_draw_color(*BLACK)
        self.set_line_width(0.4)
        self.line(MARGIN, self.get_y() - 1, MARGIN + 22, self.get_y() - 1)
        self.ln(2)

    def body(self, text, color=BLACK, size=10.5):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*color)
        self.multi_cell(CONTENT_W, 5.4, text)
        self.ln(1)

    def kv(self, key, value):
        self.set_font("Helvetica", "B", 9.5)
        self.set_text_color(*GREY)
        self.cell(38, 6, key)
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(*BLACK)
        self.multi_cell(CONTENT_W - 38, 6, value or "-")


# --------------------------------------------------------------------------- #
# Helpers to shape data
# --------------------------------------------------------------------------- #
def derive_milestones_from_tasks(tasks_df: pd.DataFrame) -> list:
    """When no explicit milestones_df, infer them from the task 'milestone' column."""
    df = tasks_df[tasks_df["milestone"].astype(str).str.strip() != ""]
    out = []
    for ms, grp in df.groupby("milestone"):
        dues = [d for d in grp["due_date"] if isinstance(d, date)]
        out.append({
            "milestone": ms,
            "date": max(dues) if dues else None,
            "start": min([d for d in grp["start_date"] if isinstance(d, date)] + dues) if dues else None,
            "client_review": bool(grp.get("is_client_review", pd.Series([False])).any()),
            "deliverables": list(grp["task"]),
        })
    out.sort(key=lambda m: (m["date"] is None, m["date"] or date.max))
    return out


# --------------------------------------------------------------------------- #
# 1) CLIENT MILESTONES PDF
# --------------------------------------------------------------------------- #
def generate_client_milestones_pdf(project_name: str, tasks_df: pd.DataFrame,
                                   output_path: str,
                                   milestones: Optional[list] = None,
                                   meta: Optional[dict] = None) -> str:
    """
    Build a clean client-facing milestones document. `milestones` is an optional
    list of dicts (from data_parser.milestones_df.to_dict('records')); if omitted
    we infer them from the tasks. `meta` is the project info block.
    """
    proj = tasks_df[tasks_df["project"] == project_name] if "project" in tasks_df else tasks_df
    if milestones is None:
        milestones = derive_milestones_from_tasks(proj)
    meta = meta or {}

    pdf = BadgerPDF()
    pdf.doc_kicker = "CLIENT UPDATE"
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(MARGIN, 32, MARGIN)
    pdf.add_page()

    # Title block
    pdf.h1(project_name)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 6, f"Project timeline & key moments  ·  prepared {_fmt_date(date.today())}")
    pdf.ln(10)

    if meta.get("client"):
        pdf.kv("Client", meta.get("client", ""))
    if meta.get("project_lead"):
        pdf.kv("T+W Lead", meta.get("project_lead", ""))
    if meta.get("deliverables"):
        pdf.kv("Scope", meta.get("deliverables", ""))
    pdf.ln(2)

    # Warm intro in Badger's voice
    pdf.body(
        "Here's where the work is headed and the moments we'll bring you in. "
        "Each milestone below shows what we're delivering and when. Where your "
        "review is needed, we've flagged it clearly so nothing waits on a surprise.",
        color=BLACK)

    # Milestones timeline (table-style)
    pdf.h2("Milestone Timeline")
    if not milestones:
        pdf.body("Milestones will appear here once dates are set in the plan.", color=GREY)
    else:
        for i, m in enumerate(milestones, 1):
            # row header line
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*BLACK)
            review = "   ·   CLIENT REVIEW" if m.get("client_review") else ""
            pdf.cell(0, 7, f'{i}.  {m["milestone"]}')
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*GREY)
            date_line = f'Target: {_fmt_date(m.get("date"))}'
            if m.get("start"):
                date_line = f'{_fmt_date(m.get("start"))}  ->  {_fmt_date(m.get("date"))}'
            pdf.cell(0, 5, date_line + (("   |   " + review.strip()) if review else ""))
            pdf.ln(6)
            # deliverables
            if m.get("deliverables"):
                pdf.set_font("Helvetica", "", 9.5)
                pdf.set_text_color(*BLACK)
                for d in m["deliverables"][:8]:
                    pdf.cell(6)
                    pdf.cell(0, 5, f"-  {d}")
                    pdf.ln(5)
            if m.get("client_review"):
                pdf.set_font("Helvetica", "B", 8.5)
                pdf.set_text_color(*AMBER)
                pdf.cell(6)
                pdf.cell(0, 5, ">> We'll need your feedback at this milestone.")
                pdf.ln(5)
            pdf.set_draw_color(*LIGHT)
            pdf.line(MARGIN, pdf.get_y() + 1, PAGE_W - MARGIN, pdf.get_y() + 1)
            pdf.ln(4)

    # What we need from the client
    reviews = [m for m in milestones if m.get("client_review")]
    pdf.h2("What We'll Need From You")
    if reviews:
        pdf.body("To keep momentum, we'll come to you for sign-off at these points:")
        for m in reviews:
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(*BLACK)
            pdf.cell(6)
            pdf.cell(0, 5.5, f'-  {m["milestone"]} - around {_fmt_date(m.get("date"))}')
            pdf.ln(5.5)
    else:
        pdf.body("No formal client reviews are flagged in the current plan. "
                 "We'll still keep you updated at each milestone.")

    # Sign-off
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*BLACK)
    pdf.multi_cell(CONTENT_W, 5.4,
                   "We'll keep this moving and flag anything early. Questions any time.")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "- Badger, on behalf of T+W")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    pdf.output(output_path)
    return output_path


# --------------------------------------------------------------------------- #
# 2) CAPACITY REPORT PDF (internal)
# --------------------------------------------------------------------------- #
def _as_records(utilization_data) -> list:
    if isinstance(utilization_data, pd.DataFrame):
        return utilization_data.to_dict("records")
    return list(utilization_data or [])


def generate_capacity_report_pdf(start_date, end_date, utilization_data,
                                 flags, recommendations, output_path: str,
                                 as_of: Optional[date] = None) -> str:
    """
    utilization_data: list/DataFrame of {person, allocated_hours, capacity_in_range
                      (or capacity), utilization, band}
    flags: list from scheduler.flag_overallocations
    recommendations: list of strings, OR reallocation-suggestion dicts.
    """
    rows = _as_records(utilization_data)
    as_of = as_of or date.today()

    pdf = BadgerPDF()
    pdf.doc_kicker = "INTERNAL · CAPACITY"
    pdf.doc_confidential = True
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(MARGIN, 32, MARGIN)
    pdf.add_page()

    pdf.h1("Capacity Report")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 6, f"{_fmt_date(start_date)}  ->  {_fmt_date(end_date)}   ·   generated {_fmt_date(as_of)}")
    pdf.ln(10)

    # Badger's direct assessment (auto-written from the numbers)
    over = [r for r in rows if str(r.get("band")) == "red"]
    watch = [r for r in rows if str(r.get("band")) == "amber"]
    under = [r for r in rows if str(r.get("band")) == "green"]
    avg = round(sum(float(r.get("utilization", 0)) for r in rows) / len(rows), 1) if rows else 0
    assessment = (
        f"Team is averaging {avg}% utilisation across the window. "
        f"{len(over)} over capacity, {len(watch)} on the line, {len(under)} with room. "
    )
    if over:
        assessment += f"Priority: relieve {', '.join(r['person'] for r in over[:4])}. "
    elif watch:
        assessment += "No one's over, but keep an eye on the amber names before they tip. "
    else:
        assessment += "Healthy balance - capacity available for new work. "
    pdf.h2("Badger's Read")
    pdf.body(assessment)

    # Utilisation bars (native)
    pdf.h2("Team Utilisation")
    if not rows:
        pdf.body("No allocated work in this window.", color=GREY)
    else:
        rows_sorted = sorted(rows, key=lambda r: -float(r.get("utilization", 0)))
        label_w, bar_w, bar_h = 34, 110, 5.0
        for r in rows_sorted:
            util = float(r.get("utilization", 0))
            band = r.get("band", "green")
            y = pdf.get_y()
            # label
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*BLACK)
            pdf.set_xy(MARGIN, y)
            pdf.cell(label_w, bar_h, str(r.get("person", ""))[:20])
            # track
            track_x = MARGIN + label_w
            pdf.set_fill_color(*LIGHT)
            pdf.rect(track_x, y + 0.6, bar_w, bar_h - 1.2, style="F")
            # 100% reference tick
            pdf.set_draw_color(*GREY)
            pdf.set_line_width(0.2)
            tick = track_x + min(bar_w, bar_w * 100 / 150.0)
            pdf.line(tick, y, tick, y + bar_h)
            # filled portion (scale: 150% spans the full track)
            fill = max(0.0, min(bar_w, bar_w * util / 150.0))
            pdf.set_fill_color(*BAND_RGB.get(band, GREY))
            pdf.rect(track_x, y + 0.6, fill, bar_h - 1.2, style="F")
            # value
            pdf.set_xy(track_x + bar_w + 3, y)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*BAND_RGB.get(band, BLACK))
            pdf.cell(20, bar_h, f"{util:.0f}%")
            pdf.ln(bar_h + 1.5)
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*GREY)
        pdf.cell(0, 4, "Track spans 0-150%. Grey tick = 100% capacity. "
                       "Green ok · amber watch · red over.")
        pdf.ln(6)

    # Overallocation detail
    if flags:
        pdf.h2("Overallocations - Detail")
        for f in flags[:12]:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(*BAND_RGB.get(f.get("severity"), BLACK))
            pdf.cell(0, 6, f'{f["person"]}  ·  {_fmt_date(f["date"])}  ·  '
                           f'{f["allocated_hours"]}h / {f["capacity"]}h = {f["utilization"]:.0f}%')
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*BLACK)
            for t in f.get("tasks", [])[:5]:
                pdf.cell(6)
                hrs = f' ({t["hours"]:.0f}h)' if t.get("hours") else ""
                pdf.cell(0, 5, f'-  {t["task"]}{hrs}  ·  {t["project"]}')
                pdf.ln(5)
            pdf.ln(2)

    # Recommendations
    pdf.h2("Recommended Actions")
    recs = recommendations or []
    if not recs:
        pdf.body("No actions required right now. Hold the line.", color=GREY)
    else:
        for rec in recs[:15]:
            if isinstance(rec, dict):
                text = (f'Move "{rec.get("task")}" ({rec.get("hours")}h) from '
                        f'{rec.get("from_person")} to {rec.get("to_person")} '
                        f'on {_fmt_date(rec.get("date"))} - {rec.get("reason","")}')
            else:
                text = str(rec)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(*BLACK)
            pdf.cell(5)
            pdf.multi_cell(CONTENT_W - 5, 5.2, f"-  {text}")
            pdf.ln(0.5)

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*BLACK)
    pdf.cell(0, 6, "- Badger")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    pdf.output(output_path)
    return output_path


# --------------------------------------------------------------------------- #
# Markdown / HTML export (quick sharing)
# --------------------------------------------------------------------------- #
def client_milestones_markdown(project_name, tasks_df, milestones=None, meta=None) -> str:
    proj = tasks_df[tasks_df["project"] == project_name] if "project" in tasks_df else tasks_df
    milestones = milestones if milestones is not None else derive_milestones_from_tasks(proj)
    meta = meta or {}
    L = [f"# {project_name}", "",
         f"*Project timeline & key moments - prepared {_fmt_date(date.today())}*", ""]
    if meta.get("client"):
        L.append(f"**Client:** {meta['client']}")
    if meta.get("project_lead"):
        L.append(f"**T+W Lead:** {meta['project_lead']}")
    L += ["", "Here's where the work is headed and the moments we'll bring you in.", "",
          "## Milestone Timeline", ""]
    for i, m in enumerate(milestones, 1):
        tag = " **- CLIENT REVIEW**" if m.get("client_review") else ""
        L.append(f"**{i}. {m['milestone']}** - {_fmt_date(m.get('date'))}{tag}")
        for d in m.get("deliverables", [])[:8]:
            L.append(f"  - {d}")
        L.append("")
    reviews = [m for m in milestones if m.get("client_review")]
    L += ["## What We'll Need From You", ""]
    if reviews:
        for m in reviews:
            L.append(f"- {m['milestone']} - around {_fmt_date(m.get('date'))}")
    else:
        L.append("- No formal reviews flagged; we'll keep you updated at each milestone.")
    L += ["", "- Badger, on behalf of T+W"]
    return "\n".join(L)


def capacity_report_markdown(start_date, end_date, utilization_data, flags, recommendations) -> str:
    rows = _as_records(utilization_data)
    over = [r for r in rows if str(r.get("band")) == "red"]
    avg = round(sum(float(r.get("utilization", 0)) for r in rows) / len(rows), 1) if rows else 0
    L = [f"# Capacity Report", f"*{_fmt_date(start_date)} → {_fmt_date(end_date)}*", "",
         f"**Badger's read:** team averaging {avg}% - {len(over)} over capacity.", "",
         "## Team Utilisation", "", "| Person | Hours | Util % | Band |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -float(r.get("utilization", 0))):
        L.append(f"| {r.get('person','')} | {r.get('allocated_hours','')} | "
                 f"{float(r.get('utilization',0)):.0f}% | {r.get('band','')} |")
    if flags:
        L += ["", "## Overallocations", ""]
        for f in flags[:12]:
            L.append(f"- **{f['person']}** {_fmt_date(f['date'])}: "
                     f"{f['allocated_hours']}h/{f['capacity']}h = {f['utilization']:.0f}%")
    L += ["", "## Recommended Actions", ""]
    for rec in (recommendations or ["No actions required right now."]):
        if isinstance(rec, dict):
            L.append(f"- Move \"{rec.get('task')}\" from {rec.get('from_person')} "
                     f"to {rec.get('to_person')} - {rec.get('reason','')}")
        else:
            L.append(f"- {rec}")
    L += ["", "- Badger"]
    return "\n".join(L)


def markdown_to_html(md_text: str, title: str = "Badger Report") -> str:
    """Minimal, clean HTML wrapper (no external CSS) for quick sharing."""
    import html
    body = []
    for line in md_text.splitlines():
        s = line.rstrip()
        if s.startswith("# "):
            body.append(f"<h1>{html.escape(s[2:])}</h1>")
        elif s.startswith("## "):
            body.append(f"<h2>{html.escape(s[3:])}</h2>")
        elif s.startswith("- ") or s.startswith("  - "):
            body.append(f"<li>{html.escape(s.strip()[2:])}</li>")
        elif s.startswith("|"):
            body.append(f"<div class='row'>{html.escape(s)}</div>")
        elif s == "":
            body.append("<br>")
        else:
            body.append(f"<p>{html.escape(s)}</p>")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            "<style>body{font-family:Helvetica,Arial,sans-serif;max-width:760px;margin:40px auto;"
            "color:#111;line-height:1.5;padding:0 16px}h1{font-size:28px}h2{font-size:15px;"
            "text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid #111;"
            "display:inline-block;padding-bottom:2px}li{margin:2px 0}.row{font-family:monospace;"
            "font-size:12px}</style></head><body>" + "\n".join(body) + "</body></html>")


# --------------------------------------------------------------------------- #
# Manual test:  python app/utils/report_generator.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import data_parser as dp
    import scheduler as sch
    import calendar_utils as cal

    here = os.path.dirname(os.path.abspath(__file__))
    reports = os.path.join(here, "..", "..", "reports")
    data = dp.load_all(csv_file=os.path.join(here, "..", "..", "data", "sample_tasks.csv"))
    org = dp.load_organigram()

    print("=== report_generator self-test ===")

    # client milestones for one project
    p = "Giants"
    out1 = os.path.join(reports, "client_milestones_giants.pdf")
    generate_client_milestones_pdf(p, data.tasks_df, out1,
                                   meta={"client": "SuperSport", "project_lead": "Jack Davis",
                                         "deliverables": "Brand film + cutdowns"})
    print(f"1. Client PDF -> {out1} ({os.path.getsize(out1)//1024} KB)")
    md1 = client_milestones_markdown(p, data.tasks_df,
                                     meta={"client": "SuperSport", "project_lead": "Jack Davis"})
    open(os.path.join(reports, "client_milestones_giants.md"), "w").write(md1)
    print(f"   + markdown ({len(md1)} chars), preview:")
    print("   " + md1.splitlines()[0])

    # capacity report across the sample window
    bars = cal.workload_bars(data.tasks_df, org)
    flags = sch.flag_overallocations(data.tasks_df, org)
    recs = sch.suggest_reallocations(data.tasks_df, org)
    out2 = os.path.join(reports, "capacity_report.pdf")
    generate_capacity_report_pdf(date(2026, 6, 15), date(2026, 6, 30),
                                 bars, flags, recs, out2)
    print(f"2. Capacity PDF -> {out2} ({os.path.getsize(out2)//1024} KB)")
    md2 = capacity_report_markdown(date(2026,6,15), date(2026,6,30), bars, flags, recs)
    html = markdown_to_html(md2, "Capacity Report")
    open(os.path.join(reports, "capacity_report.html"), "w").write(html)
    print(f"   + markdown + html ({len(html)} chars)")
    print("\nBoth reports generated successfully.")
