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
    """Dated things from mail on one calendar date, each real thing once.

    Merged exactly as the calendar merges them (calendar_store), so a quiz
    mentioned in its announcement and in two reminders is one line in the
    evening reminder, not three - and a mention that carried the wrong date
    does not add a phantom one. Anything the student took off the calendar is
    left out of the reminder too.
    """
    import calendar_store

    store = _load(STORE_FILE, {"mails": []})
    mails = [m for m in store.get("mails", []) if isinstance(m, dict)]
    skip = hidden_ids() | {m.get("id") for m in mails
                           if m.get("category") == "Ignore" and not m.get("absolute")}
    removed = set(calendar_store.load_overrides()["removed"])
    on, off = calendar_store.build(mails, target, target, hidden_ids=skip)

    found = []
    for entry in on + [e for e in off if not any(k in removed for k in e["keys"])]:
        item = dict(entry)
        item["source"] = "mail"
        found.append(item)
    found.sort(key=lambda e: e.get("start_time") or "99:99")
    return found


def agenda(target):
    """Everything on one date: mail events first, then timetabled classes."""
    from_mail = mail_events_on(target)
    from_timetable = courses.classes_on(target)
    return from_mail, from_timetable


def relative_label(target, today=None):
    """"Tomorrow", "Today", or the weekday - whatever is true for this date."""
    today = today or datetime.now().astimezone().date()
    delta = (target - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    return target.strftime("%A")


def compose(target, from_mail, from_timetable, today=None):
    """(title, body) for the notification, or (None, None) if nothing to say."""
    if not from_mail and not from_timetable:
        return None, None

    day = target.strftime("%A %d %b")
    label = relative_label(target, today)
    lines = []

    for event in from_mail[:MAX_LINES]:
        when = event.get("start_time") or "all day"
        where = " · " + event["location"] if event.get("location") else ""
        lines.append("{}  {}{}".format(when, event["title"], where))

    # A makeup class or quiz on top of another course's lecture is the thing
    # most worth knowing the evening before, so it goes first.
    import conflicts
    for event in conflicts.annotate([dict(e) for e in from_mail]):
        warning = conflicts.describe(event)
        if warning:
            lines.insert(0, "⚠ " + warning)

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
        title = "{} ({}): {}".format(label, day, urgent[0]["title"])
    elif from_mail:
        title = "{} ({}): {} thing{} on".format(
            label, day, len(from_mail), "" if len(from_mail) == 1 else "s")
    else:
        title = "{} ({})".format(label, day)

    return title, "\n".join(lines)


NOTIFY_SCRIPT = os.path.join(SCRIPT_DIR, "notify.ps1")


def notify(title, body):
    """Show a Windows notification. Returns True if it was displayed.

    The reminder is only useful if it is still there when the student next
    looks at the screen, so notify.ps1 sends a "reminder" toast, which stays
    up until it is clicked away. It falls back to a fading tray balloon by
    itself on a machine where the toast platform is unavailable (exit 2).
    """
    if os.name != "nt":
        # macOS: an alert dialog that stays until clicked (notifications fade);
        # Linux: a critical notify-send. Printed as well, for the log.
        print(title)
        print(body)
        try:
            import platforms
            return platforms.notify_sticky(title, body) or True
        except Exception:  # noqa: BLE001 - a failed popup must not crash the task
            return True

    if not os.path.exists(NOTIFY_SCRIPT):
        print("Could not show a notification (notify.ps1 is missing).",
              file=sys.stderr)
        return False

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", NOTIFY_SCRIPT, "-Title", title, "-Body", body],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001 - a failed popup must not crash the task
        print("Could not show a notification ({}).".format(exc), file=sys.stderr)
        return False

    if result.returncode == 0:
        return True
    if result.returncode == 2:
        # Shown, but it will fade. Worth saying so: the whole point of the
        # reminder is that it waits for the student.
        print("Showed a fading balloon - the sticky toast was unavailable.",
              file=sys.stderr)
        return True

    print("Could not show a notification.", file=sys.stderr)
    if result.stderr:
        print(result.stderr.strip()[:500], file=sys.stderr)
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

    # Local time, not UTC. "Tomorrow" is a calendar word: at UTC+5:30 a run
    # after midnight would otherwise report today as tomorrow, which is
    # exactly when a student is most likely to be looking.
    target = (datetime.now().astimezone() + timedelta(days=args.days)).date()
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
