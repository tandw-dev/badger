# Google Sheets Structure for Aether — Master Project & Task Tracker

**Philosophy:** Project Managers live in Google Sheets. They update it daily. Aether reads it (via connection or CSV export/upload) and turns the raw plan into intelligence, calendar views, Slack briefs, and reports. Keep the sheet clean, consistent, and simple. No fancy formulas needed — Aether handles aggregation, calculations, and cross-project views.

## Recommended Setup (Two Main Approaches)

### Option 1: Single Master Sheet (Recommended for Start)
One big "Master Tasks" sheet + optional "Projects Summary" tab. Easy for Aether to connect to one URL/key.

**Tab 1: Tasks (Main Working Tab)**
Columns (in this exact order for easiest parsing; Aether can adapt but consistency wins):

| Column | Header (Exact) | Type/Example | Purpose & Rules |
|--------|----------------|--------------|-----------------|
| A | Project | "Acme Corp Website Redesign" or "Internal Ops Overhaul Q3" | Groups everything. Use consistent naming. |
| B | Milestone | "Discovery & Strategy" or "Design Phase" or "Development Sprint 1" | Logical phases. Client-facing or internal. |
| C | Task / Job to be Done | "Conduct stakeholder interviews (5 sessions)" or "Build homepage prototype in Figma" | Specific, actionable. One clear deliverable per row. |
| D | Assigned Person | "Sarah Chen" or "Jack Davis" or "Freelancer - DevX" | Exact match to Organigram name. Critical for resource views. |
| E | Estimated Hours | 8 or 4.5 | Realistic. This drives capacity math. Update as you learn. |
| F | Start Date | 2026-07-01 or "2026-07-01" | When work can/should begin. |
| G | Due Date | 2026-07-05 | Hard or soft deadline. Aether uses for calendar + reminders. |
| H | Status | Not Started / In Progress / Blocked / Done / On Hold | Drives progress, daily briefs, health indicators. |
| I | % Complete | 0 or 25 or 100 | Optional but powerful for weighted progress. |
| J | Dependencies / Notes | "Depends on client feedback from Milestone 1" or "Requires brand assets from client" | Free text. Aether surfaces blockers. |
| K | Client Review Milestone? | Yes / No | Flags tasks/milestones where client needs to be looped in for review/approval. Triggers client comms. |
| L | Priority (Optional) | High / Medium / Low or 1-5 | Helps daily brief prioritization. |
| M | Actual Hours (Optional) | 7.5 | For future learning — compare estimate vs reality. |

**Rules for this tab:**
- One row = one discrete task/job. No multi-person on one row (split if needed or note "shared with X").
- Dates in consistent format (YYYY-MM-DD best for parsing).
- Person names **must match exactly** the Organigram (including spelling/capitalization). This is how Aether links data.
- Update Status and % regularly — this feeds Vic's daily briefs and health dashboards.
- Archive old/completed projects by moving rows to an "Archive" tab or filtering them out (Aether can ignore Status=Done older than X days if you want).

**Tab 2: Projects Summary (Optional but Powerful)**
High-level view for quick oversight and client milestone docs.

Columns:
- Project
- Client / Internal?
- Overall Status (Green/Yellow/Red or custom)
- Start Date (overall)
- Target End Date
- Key Milestones (or link to Tasks tab)
- Total Estimated Hours (Aether can SUMIF from Tasks)
- Project Manager
- Current Health Notes
- Next Client Touchpoint Date

Aether can generate this summary from Tasks tab if you prefer minimal maintenance.

### Option 2: One Sheet per Project (Scales Better Long-Term)
- Folder in Google Drive: "Aether Project Sheets"
- Each new project gets its own Sheet copied from a **Project Template**.
- Aether connects to the folder or you provide a list of active sheet URLs/keys in a "Active Projects Index" sheet.
- Same column structure as above in each project's Tasks tab.
- Bonus: Each project sheet can have its own "Milestones" summary tab that Aether turns into beautiful client PDFs.

**Project Template Recommendation:** Create one master template sheet with the columns above + instructions in a "How to Use" tab. Duplicate for every new project.

## How Aether Interacts With the Sheet(s)
1. **Connection (Best):** Use Streamlit gsheets-connection or gspread with service account (one-time setup — Claude will guide you through creating the JSON key in Google Cloud Console, share the sheet with the service email, done). Live read (and optional write-back for status if you want).
2. **Fallback:** Export the Tasks tab as CSV → Upload to Aether Studio dashboard. Works offline/local too.
3. **Parsing Logic (Aether does this):**
   - Group by Project + Milestone for timelines.
   - Group by Assigned Person + Date for daily/weekly workload and calendar events.
   - Calculate per-person daily total hours, utilization % vs capacity from Organigram.
   - Identify upcoming milestones (Due Date within 7 days or Client Review = Yes).
   - Detect issues: Overallocation, tasks with no assignee, past-due Not Started, etc.

## Sample Data Snippet (First 5 Rows Example)

| Project | Milestone | Task / Job to be Done | Assigned Person | Estimated Hours | Start Date | Due Date | Status | % Complete | Dependencies / Notes | Client Review Milestone? |
|---------|-----------|-----------------------|-----------------|-----------------|------------|----------|--------|------------|----------------------|--------------------------|
| Acme Corp Website | Discovery | Stakeholder interviews (5 sessions) | Jack Davis | 6 | 2026-07-06 | 2026-07-10 | In Progress | 40 | Need calendar access from client | Yes |
| Acme Corp Website | Discovery | Synthesize findings into strategy deck | Sarah Chen | 8 | 2026-07-08 | 2026-07-12 | Not Started | 0 | Depends on interviews | No |
| Acme Corp Website | Design | Low-fidelity wireframes for 8 key pages | Sarah Chen | 12 | 2026-07-13 | 2026-07-20 | Not Started | 0 | | Yes |
| Internal Ops | Process Mapping | Map current client onboarding flow | PM Lead | 4 | 2026-07-01 | 2026-07-03 | Done | 100 | | No |
| Internal Ops | Automation | Build Zapier/Make scenarios for 3 workflows | Dev Lead | 10 | 2026-07-05 | 2026-07-15 | In Progress | 30 | Needs access to current tools | No |

## Pro Tips for PMs Using the Sheet
- **Update daily or at least end-of-day:** Status changes, % complete, new tasks, actual hours if tracking.
- **Be specific in Task column:** "Revise homepage hero section based on feedback v2" > "Homepage work".
- **Use consistent Project names:** This powers filtering and reporting.
- **Flag Client Review early:** Set "Yes" on the milestone/task where you want Vic to help draft client update emails.
- **Don't delete rows** — archive or change Status to Done. History helps future estimation accuracy.
- **Add rows freely** — Aether handles variable numbers of tasks.

## Getting Started Prompt for Claude (to generate your first sheet or template)
"Create a ready-to-copy Google Sheets template structure for Aether project tracking. Include the exact column headers, sample data for 2 projects (one client, one internal), and a second 'Projects Summary' tab. Also provide step-by-step instructions for a non-technical PM to set it up and connect it to a Streamlit app later."

This file (google_sheets_structure.md) lives in your Aether project. Share it with your PMs. It's the contract between human planning and Aether intelligence.

Vic reads this sheet like a conductor reads a score — every note (task) in context of the whole symphony (portfolio) and the players (resources). Keep it accurate, and the music stays beautiful.