"""
data_parser.py — Parses Google Sheets / CSV into clean pandas DataFrames.

Responsibilities (built in Prompt 2.1):
- Accept a Google Sheet connection (gspread / st-gsheets-connection) OR an uploaded CSV.
- Parse the Tasks tab, standardize columns, parse dates, fuzzy-match names to organigram.
- Add derived columns (week no., day, utilization impact, is_client_review, is_overdue).
- Return tasks_df, daily_workload_df, project_summary_df, person_capacity_df.

>>> SKELETON ONLY <<<
"""
