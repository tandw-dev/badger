"""
daily_brief_job.py — Badger's automated morning brief job.

Designed to be run on a schedule (cron, GitHub Actions, Make.com, etc.).
Reads the live Sheet, builds each person's daily brief, and either:

  * DIGEST mode (default, human-in-the-loop): posts ONE summary to a Slack
    channel so a human can glance and approve, sending nothing to individuals.
  * SEND mode (--send): DMs each person their brief directly. Use only once
    you trust the output.

Secrets:
  Reads .streamlit/secrets.toml if present, else env vars
  SLACK_BOT_TOKEN / SLACK_DEFAULT_CHANNEL and the gsheets service-account JSON
  via GSHEETS_* — but the simplest path is to commit nothing and let the
  scheduler mount your secrets.toml (see README > Automation).

Usage:
  python scripts/daily_brief_job.py                 # digest preview to channel
  python scripts/daily_brief_job.py --send          # DM everyone (live)
  python scripts/daily_brief_job.py --channel "#studio"
"""

from __future__ import annotations

import argparse
import os
import sys

# make utils importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app", "utils"))

import data_parser as dp      # noqa: E402
import scheduler as sch       # noqa: E402
import slack_sender as sl     # noqa: E402


def run(send: bool = False, channel: str | None = None) -> dict:
    # 1) load live data + analysis
    bundle = dp.load_all()
    org = dp.load_organigram()
    briefs = sch.build_daily_briefs(bundle.tasks_df, org)

    if not briefs:
        print("No work due today — nothing to brief.")
        return {"briefs": 0, "sent": 0}

    cfg = sl.load_slack_config()
    digest_channel = channel or cfg.get("default_channel", "#badger-test")

    if send:
        # SEND mode — DM each person who has a Slack handle
        results = sl.send_briefs_for_all(briefs, dry_run=False)
        sent = sum(1 for r in results if r.get("ok") and not r.get("preview"))
        for r in results:
            status = "sent" if r.get("ok") else f"skip ({r.get('error')})"
            print(f"  {r['person']}: {status}")
        print(f"Done. {sent}/{len(results)} briefs sent.")
        return {"briefs": len(briefs), "sent": sent}

    # DIGEST mode — one approval message to the channel
    lines = [f"*Badger — morning digest* ({len(briefs)} people have work today)",
             "_Review, then send from Badger Studio > Badger Comms._", ""]
    for person, b in briefs.items():
        flag = " :warning: over" if b["over_capacity"] else ""
        top = b["items"][0]["task"] if b["items"] else "—"
        lines.append(f"• *{person}* — {b['total_hours']:.0f}h{flag} · top: {top}")
    text = "\n".join(lines)
    res = sl.send_badger_message(digest_channel, text)
    print(f"Digest -> {digest_channel}: {'ok' if res.get('ok') else res.get('error')}")
    return {"briefs": len(briefs), "sent": 0, "digest": res.get("ok", False)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Badger daily brief job")
    ap.add_argument("--send", action="store_true",
                    help="DM each person their brief (live). Omit for digest preview.")
    ap.add_argument("--channel", default=None, help="Override the digest channel.")
    args = ap.parse_args()
    run(send=args.send, channel=args.channel)
