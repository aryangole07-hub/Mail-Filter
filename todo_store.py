#!/usr/bin/env python3
"""
todo_store.py - the To-Do list: your own tasks plus the ones your mail sets you.

Two sources, one list:

  * things you add yourself;
  * things your mail asks of you, found without a model:
      - every deadline on the calendar (calendar_store already merged the
        reminders, so one assignment is one task);
      - action sentences in shown mail - "Fill the Google form by 5 PM",
        "Upload the PPT before the tutorial" - an instruction verb aimed at the
        student plus a "by / before / until / no later than" limit.

Mail tasks are regenerated from the mail each time, so the list keeps up with
the inbox. What you do to them is remembered by a stable key: ticking one off,
removing it, or dragging it into a new place survives the next refresh, and a
removed mail task does not come back.

Stored in todos.json (gitignored).
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import calendar_store

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TODO_FILE = os.path.join(SCRIPT_DIR, "todos.json")

MAX_TEXT = 300
MAX_ACTIONS_PER_MAIL = 3
# A mail task this many days past its due date, never ticked off, drops off:
# on the real inbox a 14-day window kept two-week-old registrations at the top.
# Tasks you add yourself never drop off.
STALE_DAYS = 3

ACTION_VERBS = (r"fill(?:\s+(?:in|out|up))?|submit|upload|register|pay|complete|"
                r"send|bring|attend|carry|download|sign(?:\s+up)?|apply|book|"
                r"collect|return|update|verify|enrol+|rsvp|respond|reply|"
                r"install|join|review|read|prepare|finish|print")
LIMIT_WORDS = (r"by|before|until|till|latest\s+by|no\s+later\s+than|on\s+or\s+before|"
               r"prior\s+to|within")
ACTION_RE = re.compile(
    r"(?:^|(?<=[.!?\n]))\s*(?:please\s+|kindly\s+|all\s+students\s+(?:must|should|are\s+"
    r"required\s+to)\s+|you\s+(?:must|should|need\s+to|have\s+to|are\s+required\s+to)\s+)?"
    # "You should also upload ..." - a filler word before the instruction verb.
    r"(?:also\s+|then\s+|now\s+|kindly\s+|please\s+)?"
    r"((?:" + ACTION_VERBS + r")\b[^.!?\n]{3,160}?\b(?:" + LIMIT_WORDS + r")\b[^.!?\n]{1,80})",
    re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _now():
    return datetime.now(timezone.utc).isoformat()


def load():
    try:
        with open(TODO_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    items = [i for i in data.get("items") or [] if isinstance(i, dict) and i.get("id")]
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    order = [k for k in data.get("order") or [] if isinstance(k, str)]
    return {"items": items, "state": state, "order": order}


def _save(data):
    tmp = TODO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, TODO_FILE)


# ---------------------------------------------------------------------------
# Your own tasks
# ---------------------------------------------------------------------------

def add(text, due_date="", due_time=""):
    text = " ".join((text or "").split())[:MAX_TEXT]
    if not text:
        return None
    data = load()
    item = {"id": "user-" + uuid.uuid4().hex[:10], "text": text, "source": "user",
            "due_date": _clean_date(due_date), "due_time": _clean_time(due_time),
            "created_at": _now()}
    data["items"].append(item)
    data["order"].insert(0, item["id"])
    _save(data)
    return item


def _clean_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _clean_time(value):
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value or "")
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return ""
    return "{:02d}:{:02d}".format(int(match.group(1)), int(match.group(2)))


def update(item_id, done=None, text=None):
    """Tick, untick or rename a task - yours or one from mail."""
    data = load()
    for item in data["items"]:
        if item["id"] == item_id:
            if text is not None:
                cleaned = " ".join(text.split())[:MAX_TEXT]
                if cleaned:
                    item["text"] = cleaned
            if done is not None:
                item["done"] = bool(done)
                item["done_at"] = _now() if done else ""
            _save(data)
            return True
    if item_id.startswith("mail-"):
        entry = data["state"].setdefault(item_id, {})
        if done is not None:
            entry["done"] = bool(done)
            entry["done_at"] = _now() if done else ""
        if text is not None and " ".join(text.split()):
            entry["text"] = " ".join(text.split())[:MAX_TEXT]
        _save(data)
        return True
    return False


def delete(item_id):
    """Remove a task. A mail task is dismissed, so it does not come back."""
    data = load()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    if len(data["items"]) == before:
        if not item_id.startswith("mail-"):
            return False
        data["state"].setdefault(item_id, {})["dismissed"] = True
    data["order"] = [k for k in data["order"] if k != item_id]
    _save(data)
    return True


def reorder(ids):
    """The order you dragged the list into. Unknown ids are kept, not lost."""
    data = load()
    ids = [i for i in ids or [] if isinstance(i, str)]
    rest = [k for k in data["order"] if k not in ids]
    data["order"] = ids + rest
    _save(data)
    return True


# ---------------------------------------------------------------------------
# Tasks your mail sets you
# ---------------------------------------------------------------------------

def _key(*parts):
    raw = "|".join(" ".join(str(p or "").lower().split()) for p in parts)
    return "mail-" + re.sub(r"[^a-z0-9|]+", "-", raw)[:180]


def action_sentences(mail):
    """Instructions aimed at the student, with a limit, found in one mail."""
    text = mail.get("body_text") or TAG_RE.sub(" ", mail.get("body_html") or "")
    found, seen = [], set()
    for match in ACTION_RE.finditer(text[:20000]):
        sentence = " ".join(match.group(1).split()).rstrip(" ,;:")
        sentence = sentence[0].upper() + sentence[1:]
        low = sentence.lower()
        if low in seen or re.search(r"unsubscribe|privacy|cookie", low):
            continue
        seen.add(low)
        found.append(sentence[:MAX_TEXT])
        if len(found) >= MAX_ACTIONS_PER_MAIL:
            break
    return found


def mail_tasks(mails, today=None, hidden_ids=None):
    """Tasks from calendar deadlines and action sentences, newest mail last."""
    today = today or datetime.now().astimezone().date()
    hidden_ids = hidden_ids or set()
    tasks = {}

    start = today - timedelta(days=60)
    end = today + timedelta(days=200)
    on, off = calendar_store.build(mails, start, end, hidden_ids=hidden_ids)
    # Only deadlines that are on the calendar: the calendar already decided
    # what is must-not-miss (and the student's Add/Remove choices), so a
    # hackathon registration or a placement opening does not become a chore.
    for entry in on:
        if entry.get("kind") != "deadline":
            continue
        key = _key("deadline", entry["keys"][0] if entry.get("keys") else entry["title"])
        tasks[key] = {
            "id": key, "text": entry["title"], "source": "mail",
            "due_date": entry.get("date", ""), "due_time": entry.get("start_time", ""),
            "mail_id": entry.get("mail_id"), "mail_subject": entry.get("mail_subject", ""),
            "details": entry.get("details", ""), "links": entry.get("links") or [],
            "courses": entry.get("courses") or [], "on_calendar": entry.get("on_calendar"),
        }

    for mail in mails or []:
        if not isinstance(mail, dict) or mail.get("id") in hidden_ids:
            continue
        if mail.get("category") == "Ignore" and not mail.get("absolute"):
            continue
        for sentence in action_sentences(mail):
            key = _key("action", mail.get("id"), sentence)
            tasks[key] = {
                "id": key, "text": sentence, "source": "mail", "due_date": "",
                "due_time": "", "mail_id": mail.get("id"),
                "mail_subject": mail.get("subject", ""), "details": "", "links": [],
                "courses": mail.get("courses") or [], "received_at": mail.get("received_at", ""),
            }
    return list(tasks.values())


def build(mails, today=None, hidden_ids=None):
    """The whole list, in your order: {"open": [...], "done": [...]}."""
    today = today or datetime.now().astimezone().date()
    data = load()
    items = []

    for item in data["items"]:
        items.append(dict(item, source="user", done=bool(item.get("done"))))

    for task in mail_tasks(mails, today, hidden_ids):
        state = data["state"].get(task["id"], {})
        if state.get("dismissed"):
            continue
        task = dict(task, done=bool(state.get("done")), done_at=state.get("done_at", ""))
        if state.get("text"):
            task["text"] = state["text"]
        if task["due_date"]:
            try:
                due = datetime.strptime(task["due_date"], "%Y-%m-%d").date()
            except ValueError:
                due = None
            if due and due < today - timedelta(days=STALE_DAYS) and not task["done"]:
                continue  # long past; keeping it would bury what is still live
        items.append(task)

    position = {key: n for n, key in enumerate(data["order"])}

    def sort_key(item):
        if item["id"] in position:
            return (0, position[item["id"]], "", "")
        # Not placed by you yet: soonest due first, undated after.
        return (1, 0, item.get("due_date") or "9999-99-99", item.get("due_time") or "99:99")

    items.sort(key=sort_key)
    for item in items:
        item["overdue"] = bool(item.get("due_date") and not item["done"]
                               and item["due_date"] < today.strftime("%Y-%m-%d"))
        item["due_today"] = item.get("due_date") == today.strftime("%Y-%m-%d")
    return {"open": [i for i in items if not i["done"]],
            "done": [i for i in items if i["done"]]}
