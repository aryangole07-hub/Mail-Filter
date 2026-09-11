#!/usr/bin/env python3
"""
alerts.py - the evening reminder about tomorrow.

Reads the digest store, finds everything happening tomorrow (both timetabled
classes and dates pulled out of mail), and shows one Windows notification.
Run by a scheduled task each evening; run it by hand any time to see what is
coming.

    python alerts.py            # notify about tomorrow
    python alerts.py --days 0   # today instead
    python alerts.py --print    # print, do not notify
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import courses
import events as events_mod

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(SCRIPT_DIR, "digest_store.json")
FEEDBACK_FILE = os.path.join(SCRIPT_DIR, "feedback.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "alerts_state.json")

MAX_LINES = 6

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def hidden_ids():
    """Mail the student reported, minus anything that can never be hidden."""
    feedback = _load(FEEDBACK_FILE, {})
    reported = {r.get("id") for r in feedback.get("reports", []) or []}
    marked = {r.get("id") for r in feedback.get("important", []) or []}
    return reported - marked


def mail_events_on(target):
    """Dated things from mail, for one calendar date."""
    store = _load(STORE_FILE, {"mails": []})
    skip = hidden_ids()
    found = []
    for mail in store.get("mails", []):
        if not isinstance(mail, dict) or mail.get("id") in skip:
            continue
        if mail.get("category") == "Ignore" and not mail.get("absolute"):
            continue
        for event in events_mod.on_calendar_date(mail.get("events") or [], target):
            entry = dict(event)
            entry["mail_subject"] = mail.get("subject", "")
            entry["source"] = "mail"
            found.append(entry)
    return found


def agenda(target):
    """Everything on one date: mail events first, then timetabled classes."""
    from_mail = mail_events_on(target)
    from_timetable = courses.classes_on(target)
    return from_mail, from_timetable


def compose(target, from_mail, from_timetable):
    """(title, body) for the notification, or (None, None) if nothing to say."""
    if not from_mail and not from_timetable:
        return None, None

    day = target.strftime("%A %d %b")
    lines = []

    for event in from_mail[:MAX_LINES]:
        when = event.get("start_time") or "all day"
        where = " · " + event["location"] if event.get("location") else ""
        lines.append("{}  {}{}".format(when, event["title"], where))

    remaining = MAX_LINES - len(lines)
    if remaining > 0 and from_timetable:
        first = from_timetable[0]
        lines.append("{} classes, first is {} at {}".format(
            len(from_timetable), first["title"], first["start_time"]))

    extra = len(from_mail) - min(len(from_mail), MAX_LINES)
    if extra > 0:
        lines.append("and {} more".format(extra))

    urgent = [e for e in from_mail if e.get("kind") in ("exam", "deadline")]
    if urgent:
        title = "Tomorrow ({}): {}".format(day, urgent[0]["title"])
    elif from_mail:
        title = "Tomorrow ({}): {} thing{} on".format(
            day, len(from_mail), "" if len(from_mail) == 1 else "s")
    else:
        title = "Tomorrow ({})".format(day)

    return title, "\n".join(lines)


def notify(title, body):
    """Show a Windows notification. Returns True if it was displayed."""
    if os.name != "nt":
        print(title)
        print(body)
        return True

    # A tray balloon needs no third-party module and no install step, which
    # matters because this has to work on a friend's machine straight after
    # setup.exe with nothing else added.
    script = r"""
param([string]$Title, [string]$Body)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
$n.BalloonTipTitle = $Title
$n.BalloonTipText = $Body
$n.Visible = $true
$n.ShowBalloonTip(20000)
Start-Sleep -Seconds 12
$n.Dispose()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", script, "-Title", title, "-Body", body],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception as exc:  # noqa: BLE001 - a failed popup must not crash the task
        print("Could not show a notification ({}).".format(exc), file=sys.stderr)
        return False


def already_sent(key):
    state = _load(STATE_FILE, {})
    return state.get("last") == key


def remember(key):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"last": key, "at": datetime.now(timezone.utc).isoformat()}, fh)
    os.replace(tmp, STATE_FILE)


def main():
    parser = argparse.ArgumentParser(description="Remind about tomorrow.")
    parser.add_argument("--days", type=int, default=1,
                        help="How many days ahead to look. Default 1 (tomorrow).")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print the agenda instead of notifying.")
    parser.add_argument("--force", action="store_true",
                        help="Notify even if the same day was already announced.")
    args = parser.parse_args()

    target = (datetime.now(timezone.utc) + timedelta(days=args.days)).date()
    from_mail, from_timetable = agenda(target)
    title, body = compose(target, from_mail, from_timetable)

    if not title:
        print("Nothing scheduled for {}.".format(target))
        return 0

    if args.print_only:
        print(title)
        print(body)
        return 0

    key = target.isoformat()
    if already_sent(key) and not args.force:
        print("Already announced {}. Use --force to repeat.".format(key))
        return 0

    if notify(title, body):
        remember(key)
        print("Notified about {}.".format(key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
