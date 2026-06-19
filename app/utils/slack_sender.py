"""
slack_sender.py — Badger's voice on Slack (slack_sdk).

Badger speaks the way main_brain.md demands: calm authority, short sentences,
strong verbs, scannable bullets, no corporate fluff — and every actionable
message ends with an open door ("Reply with blockers and I'll adjust.").

What's here:
  * load_slack_config()                      — bot token + default channel from secrets
  * send_badger_message(target, text, blocks, dry_run)  — post as Badger
        (send_vic_message kept as an alias for older prompts)
  * generate_daily_brief(person, tasks, total_hours, context)  — compose only
  * generate_milestone_reminder(person, milestone, project, due, status) — compose only
  * send_daily_brief(...) / send_milestone_reminder(...)        — compose + send
  * schedule_message(target, text, post_at)  — native Slack scheduling
  * Preview mode everywhere via dry_run=True (returns text/blocks, sends nothing)

slack_sdk is imported lazily so this module loads (and previews work) even where
the package or a token isn't present — handy for the dashboard's preview flow
and for offline testing.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time
from typing import Optional

# Badger's Slack identity (requires chat:write.customize scope to override; if not
# granted, Slack ignores these and posts as the app — text still reads as Badger).
BADGER_USERNAME = "Badger"
BADGER_ICON = ":badger:"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_slack_config(secrets: Optional[dict] = None) -> dict:
    """
    Return {'bot_token', 'default_channel'}.

    Order of preference:
      1) explicit `secrets` dict (e.g. st.secrets['slack'])
      2) local .streamlit/secrets.toml  [slack] block
      3) env vars SLACK_BOT_TOKEN / SLACK_DEFAULT_CHANNEL
    """
    if secrets:
        return {"bot_token": secrets.get("bot_token"),
                "default_channel": secrets.get("default_channel", "#general")}
    # local toml
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", ".streamlit", "secrets.toml")
    if os.path.exists(path):
        try:
            try:
                import tomllib
                with open(path, "rb") as f:
                    data = tomllib.load(f)
            except ModuleNotFoundError:
                import tomli
                with open(path, "rb") as f:
                    data = tomli.load(f)
            slack = data.get("slack", {})
            if slack.get("bot_token"):
                return {"bot_token": slack.get("bot_token"),
                        "default_channel": slack.get("default_channel", "#general")}
        except Exception:
            pass
    return {"bot_token": os.environ.get("SLACK_BOT_TOKEN"),
            "default_channel": os.environ.get("SLACK_DEFAULT_CHANNEL", "#general")}


def _client(secrets: Optional[dict] = None):
    """Build a slack_sdk WebClient. Raises a clear error if not configured."""
    cfg = load_slack_config(secrets)
    if not cfg.get("bot_token") or "your-bot" in str(cfg["bot_token"]):
        raise RuntimeError("Slack bot token not set. Add [slack] bot_token to "
                           ".streamlit/secrets.toml (see Admin > Setup).")
    from slack_sdk import WebClient
    return WebClient(token=cfg["bot_token"]), cfg


def _fmt_date(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%a %d %b")
    return str(d) if d else "TBC"


# --------------------------------------------------------------------------- #
# Low-level send
# --------------------------------------------------------------------------- #
def send_badger_message(channel_or_user_id: str, text: str,
                        blocks: Optional[list] = None,
                        secrets: Optional[dict] = None,
                        dry_run: bool = False) -> dict:
    """
    Post `text` (and optional Block Kit `blocks`) as Badger.

    Returns a confirmation dict:
      {"ok": bool, "preview": bool, "channel": ..., "ts": ..., "error": ...}
    With dry_run=True nothing is sent — the payload is echoed back for preview.
    """
    payload = {"channel": channel_or_user_id, "text": text,
               "username": BADGER_USERNAME, "icon_emoji": BADGER_ICON}
    if blocks:
        payload["blocks"] = blocks

    if dry_run:
        return {"ok": True, "preview": True, "channel": channel_or_user_id,
                "text": text, "blocks": blocks}

    try:
        client, _ = _client(secrets)
        resp = client.chat_postMessage(**payload)
        return {"ok": True, "preview": False, "channel": resp["channel"],
                "ts": resp["ts"]}
    except RuntimeError as e:                      # not configured
        return {"ok": False, "preview": False, "error": str(e)}
    except Exception as e:                          # SlackApiError etc.
        err = getattr(getattr(e, "response", None), "data", {}) or {}
        return {"ok": False, "preview": False,
                "error": err.get("error", str(e))}


# Backwards-compatible alias (the playbook prompt calls it send_vic_message)
send_vic_message = send_badger_message


def schedule_message(channel_or_user_id: str, text: str, post_at: datetime,
                     blocks: Optional[list] = None,
                     secrets: Optional[dict] = None) -> dict:
    """
    Schedule a message with Slack's native scheduler (chat.scheduleMessage).
    `post_at` is a datetime; must be in the future, within ~120 days.
    For a recurring 'every morning' job, drive send_daily_brief() from cron,
    APScheduler, or GitHub Actions (see README 'Daily Ritual').
    """
    try:
        client, _ = _client(secrets)
        kwargs = {"channel": channel_or_user_id, "text": text,
                  "post_at": int(post_at.timestamp()),
                  "username": BADGER_USERNAME, "icon_emoji": BADGER_ICON}
        if blocks:
            kwargs["blocks"] = blocks
        resp = client.chat_scheduleMessage(**kwargs)
        return {"ok": True, "scheduled_message_id": resp.get("scheduled_message_id"),
                "post_at": post_at.isoformat()}
    except Exception as e:
        err = getattr(getattr(e, "response", None), "data", {}) or {}
        return {"ok": False, "error": err.get("error", str(e))}


# --------------------------------------------------------------------------- #
# Composition — Badger's daily brief
# --------------------------------------------------------------------------- #
def generate_daily_brief(person_name: str, tasks_for_today: list,
                         total_hours: Optional[float] = None,
                         context_from_projects: Optional[str] = None,
                         capacity: Optional[float] = None,
                         brief_date: Optional[date] = None) -> dict:
    """
    Compose Badger's daily brief. Returns {"text", "blocks"} — does NOT send.

    `tasks_for_today` items may be dicts with keys:
        task, project, hours, why   (why = "client review", "overdue (date)" ...)
    or plain strings. Tone is strict main_brain.md: calm, direct, prioritised,
    ends with an invitation to reply.
    """
    brief_date = brief_date or date.today()
    first = person_name.split()[0] if person_name else "there"

    # normalise items
    norm = []
    for t in tasks_for_today:
        if isinstance(t, dict):
            norm.append(t)
        else:
            norm.append({"task": str(t)})
    if total_hours is None:
        total_hours = sum(float(t.get("hours") or 0) for t in norm)

    # ---- plain text (fallback + notification preview) ----
    lines = [f"Good morning {first}. Here's your focus for {_fmt_date(brief_date)}.", ""]
    for t in norm:
        hrs = f' ({t["hours"]:.0f}h)' if t.get("hours") else ""
        why = f' — {t["why"]}' if t.get("why") else ""
        proj = f' · {t["project"]}' if t.get("project") else ""
        lines.append(f'• {t["task"]}{hrs}{proj}{why}')
    lines.append("")
    cap_note = f" of {capacity:.0f}h" if capacity else ""
    if total_hours:
        lines.append(f"That's ~{total_hours:.0f}h{cap_note} today.")
    if capacity and total_hours and total_hours > capacity:
        lines.append("Heads up — that's over a full day. Tell me what to move.")
    if context_from_projects:
        lines.append(context_from_projects)
    lines.append("")
    lines.append("Reply with blockers or updates and I'll adjust. — Badger")
    text = "\n".join(lines)

    # ---- Block Kit (richer in-Slack rendering) ----
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"Your focus · {_fmt_date(brief_date)}"}},
        {"type": "section",
         "text": {"type": "mrkdwn", "text": f"Good morning *{first}*. Here's what matters today."}},
    ]
    bullet_lines = []
    for t in norm:
        hrs = f' ({t["hours"]:.0f}h)' if t.get("hours") else ""
        proj = f' · _{t["project"]}_' if t.get("project") else ""
        why = f'  ⚠️ {t["why"]}' if t.get("why") else ""
        bullet_lines.append(f'• *{t["task"]}*{hrs}{proj}{why}')
    if bullet_lines:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "\n".join(bullet_lines)}})
    if total_hours:
        ctx = f"~{total_hours:.0f}h{cap_note} today"
        if capacity and total_hours > capacity:
            ctx += "  ·  over a full day — tell me what to move"
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": ctx}]})
    if context_from_projects:
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": context_from_projects}]})
    blocks.append({"type": "divider"})
    blocks.append({"type": "context",
                   "elements": [{"type": "mrkdwn",
                                 "text": "Reply with blockers or updates and I'll adjust. — *Badger*"}]})
    return {"text": text, "blocks": blocks}


def generate_milestone_reminder(person_name: str, milestone: str, project: str,
                                due_date, current_status: str = "",
                                days_out: Optional[int] = None) -> dict:
    """
    Compose a proactive milestone heads-up. Returns {"text","blocks"} — no send.
    Calm, specific, offers help — never nags.
    """
    first = person_name.split()[0] if person_name else "there"
    when = _fmt_date(due_date)
    window = ""
    if days_out is not None:
        window = "today" if days_out == 0 else (f"in {days_out} day{'s' if days_out != 1 else ''}")
    status = current_status or "not yet updated"

    text = (f"Heads up {first} — *{milestone}* for *{project}* is due {when}"
            f"{(' (' + window + ')') if window else ''}.\n"
            f"Current status: {status}.\n"
            f"Need anything from me or the team to land it? Reply and I'll move. — Badger")

    blocks = [
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f"⏳ *Milestone heads-up*\n*{milestone}* — _{project}_\n"
                           f"Due *{when}*{(' · ' + window) if window else ''}  ·  "
                           f"status: {status}")}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": "Need anything to land it? Reply and I'll move. — *Badger*"}]},
    ]
    return {"text": text, "blocks": blocks}


# --------------------------------------------------------------------------- #
# Compose + send (convenience wrappers)
# --------------------------------------------------------------------------- #
def send_daily_brief(target: str, person_name: str, tasks_for_today: list,
                     total_hours: Optional[float] = None,
                     context_from_projects: Optional[str] = None,
                     capacity: Optional[float] = None,
                     secrets: Optional[dict] = None, dry_run: bool = False) -> dict:
    msg = generate_daily_brief(person_name, tasks_for_today, total_hours,
                               context_from_projects, capacity)
    return send_badger_message(target, msg["text"], msg["blocks"],
                               secrets=secrets, dry_run=dry_run)


def send_milestone_reminder(target: str, person_name: str, milestone: str,
                            project: str, due_date, current_status: str = "",
                            days_out: Optional[int] = None,
                            secrets: Optional[dict] = None, dry_run: bool = False) -> dict:
    msg = generate_milestone_reminder(person_name, milestone, project, due_date,
                                      current_status, days_out)
    return send_badger_message(target, msg["text"], msg["blocks"],
                               secrets=secrets, dry_run=dry_run)


def send_briefs_for_all(briefs: dict, secrets: Optional[dict] = None,
                        dry_run: bool = True) -> list:
    """
    Take scheduler.build_daily_briefs() output {person: {...}} and send/preview
    a brief to each person who has a Slack handle. Defaults to dry_run (preview)
    so a human approves before anything goes out (Human-in-the-Loop).
    """
    results = []
    for person, b in briefs.items():
        target = b.get("slack") or ""
        msg = generate_daily_brief(person, b.get("items", []),
                                   b.get("total_hours"), capacity=b.get("capacity"))
        if not target:
            results.append({"person": person, "ok": False,
                            "error": "no Slack handle in organigram", "preview": dry_run,
                            "text": msg["text"]})
            continue
        res = send_badger_message(target, msg["text"], msg["blocks"],
                                  secrets=secrets, dry_run=dry_run)
        res["person"] = person
        res.setdefault("text", msg["text"])
        results.append(res)
    return results


def test_connection(secrets: Optional[dict] = None) -> dict:
    """auth.test — confirm the token works and show which bot/workspace."""
    try:
        client, cfg = _client(secrets)
        r = client.auth_test()
        return {"ok": True, "bot": r.get("user"), "team": r.get("team"),
                "default_channel": cfg.get("default_channel")}
    except Exception as e:
        err = getattr(getattr(e, "response", None), "data", {}) or {}
        return {"ok": False, "error": err.get("error", str(e))}


# --------------------------------------------------------------------------- #
# Manual test:  python app/utils/slack_sender.py   (preview only — no sending)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("=== slack_sender self-test (preview / dry-run only) ===\n")

    sample_tasks = [
        {"task": "Director's treatment v2", "project": "Giants", "hours": 6, "why": "client review"},
        {"task": "Archive footage audit", "project": "Rugby's Greatest Rivalry",
         "hours": 4, "why": "overdue (17 Jun)"},
        {"task": "Broadcaster deck", "project": "Greens and Gold", "hours": 3},
    ]
    brief = generate_daily_brief("Kholiwe Dlamini", sample_tasks,
                                 capacity=7, brief_date=date(2026, 6, 18))
    print("----- EXAMPLE BADGER DAILY BRIEF (plain text) -----")
    print(brief["text"])
    print("\n----- Block count:", len(brief["blocks"]), "-----\n")

    rem = generate_milestone_reminder("Busi", "Production Lock", "Giants",
                                      date(2026, 6, 22), "In Progress", days_out=4)
    print("----- EXAMPLE MILESTONE REMINDER -----")
    print(rem["text"])

    print("\n----- dry-run send (no token needed) -----")
    res = send_badger_message("#studio", brief["text"], brief["blocks"], dry_run=True)
    print({k: res[k] for k in ("ok", "preview", "channel")})

    print("\n----- live connection check (will show 'not configured' until token set) -----")
    print(test_connection())
