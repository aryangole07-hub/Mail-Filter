#!/usr/bin/env python3
"""
calendar_store.py - what goes on the calendar, and the student's say over it.

The calendar used to show every lecture from the timetable plus every date the
model found in mail. The student asked for the opposite: no classes at all,
only the things they cannot afford to miss - assignments due that day (once,
with where to find the instructions and the mail they came from) and quizzes
or exams (with their portions). Everything else found in mail is offered, not
imposed: "Add to cal" puts it on, "Remove from cal" takes anything off.

Rules:

  * automatically on: exams, and deadlines from anything that is not a Fests
    mail (a hackathon registration deadline is an offer, not an obligation);
  * everything else dated in a visible mail is a suggestion;
  * the student's choices win, both ways, and are kept in
    calendar_overrides.json (gitignored);
  * the same assignment mentioned in three reminder mails is one entry, with
    all three mails listed as its sources.
"""

import json
import os
import re
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OVERRIDES_FILE = os.path.join(SCRIPT_DIR, "calendar_overrides.json")

MUST_NOT_MISS_KINDS = {"exam", "deadline"}
MAX_LINKS = 3

URL_RE = re.compile(r"""https?://[^\s<>"'()\]]+""", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["'](https?://[^"']+)["']""", re.I)

# Links worth offering as "where the instructions are".
USEFUL_LINK_HINTS = ("classroom.google.com", "drive.google.com", "docs.google.com",
                     "forms.gle", "forms.google.com", "sites.google.com",
                     "lms", "moodle", "bits-pilani.ac.in", "onedrive", "sharepoint",
                     "dropbox", "notion.so", "github.com", "unstop.com")
# Links that are never instructions: footers, trackers, images.
USELESS_LINK_HINTS = ("unsubscribe", "list-manage", "mailtrack", "facebook.com",
                      "twitter.com", "x.com/", "instagram.com", "linkedin.com",
                      "youtube.com", "whatsapp", "t.me/", "/track", "open.php",
                      "googleusercontent", "gstatic", "privacy", "preferences")
IMAGE_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico)(\?|$)", re.I)

# Words that differ between "Assignment 2 due" and "Reminder: Assignment 2
# deadline" without making them different things.
TITLE_NOISE = re.compile(
    r"\b(reminder|due|deadline|submission|submit|last\s+date|extended|today|"
    r"tomorrow|on|by|for|the|of|is|a|an)\b|[^\w\s]", re.I)


def event_key(mail_id, event):
    return "{}|{}|{}".format(mail_id, event.get("date", ""),
                             " ".join((event.get("title") or "").lower().split()))


def same_thing_key(event):
    title = TITLE_NOISE.sub(" ", (event.get("title") or "").lower())
    return "{}|{}".format(event.get("date", ""), " ".join(title.split()))


def load_overrides():
    try:
        with open(OVERRIDES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"added": [k for k in data.get("added") or [] if isinstance(k, str)],
            "removed": [k for k in data.get("removed") or [] if isinstance(k, str)]}


def _save_overrides(data):
    tmp = OVERRIDES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, OVERRIDES_FILE)


def set_on_calendar(keys, on):
    """Put entries on (on=True) or take them off. `keys` may be one or many."""
    if isinstance(keys, str):
        keys = [keys]
    data = load_overrides()
    added, removed = set(data["added"]), set(data["removed"])
    for key in keys:
        if on:
            added.add(key)
            removed.discard(key)
        else:
            removed.add(key)
            added.discard(key)
    data = {"added": sorted(added), "removed": sorted(removed)}
    _save_overrides(data)
    return data


def automatically_on(event, mail):
    if event.get("kind") not in MUST_NOT_MISS_KINDS:
        return False
    if event.get("kind") == "deadline" and mail.get("category") == "Fests" \
            and not mail.get("absolute"):
        return False
    return True


def links_for(mail, event=None):
    """Where the instructions probably are, only ever links that are in the mail."""
    text = "{}\n{}".format(mail.get("body_text") or "", mail.get("body_html") or "")
    candidates = []
    if event and event.get("link"):
        candidates.append(event["link"])
    candidates += HREF_RE.findall(mail.get("body_html") or "")
    candidates += URL_RE.findall(mail.get("body_text") or "")

    useful, seen = [], set()
    for url in candidates:
        url = url.rstrip(".,;")
        low = url.lower()
        if low in seen or url not in text:
            continue  # a link the mail does not contain is never offered
        seen.add(low)
        if IMAGE_RE.search(low) or any(h in low for h in USELESS_LINK_HINTS):
            continue
        useful.append((0 if any(h in low for h in USEFUL_LINK_HINTS) else 1, url))
    useful.sort(key=lambda pair: pair[0])
    return [url for _, url in useful[:MAX_LINKS]]


def build(mails, start_date, end_date, overrides=None, hidden_ids=None):
    """(entries on the calendar, suggestions not on it), both date-sorted."""
    overrides = overrides or load_overrides()
    added, removed = set(overrides["added"]), set(overrides["removed"])
    hidden_ids = hidden_ids or set()

    on, off = {}, {}
    for mail in mails or []:
        if not isinstance(mail, dict) or mail.get("id") in hidden_ids:
            continue
        for event in mail.get("events") or []:
            if not isinstance(event, dict) or not event.get("date"):
                continue
            try:
                when = datetime.strptime(event["date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if not (start_date <= when <= end_date):
                continue

            key = event_key(mail.get("id"), event)
            is_on = key in added or (automatically_on(event, mail) and key not in removed)
            source = {"mail_id": mail.get("id"), "subject": mail.get("subject", ""),
                      "received_at": mail.get("received_at", ""), "key": key}
            bucket = on if is_on else off
            group = same_thing_key(event)

            if group in bucket:
                entry = bucket[group]
                entry["sources"].append(source)
                entry["keys"].append(key)
                for field in ("start_time", "end_time", "location", "details"):
                    if not entry.get(field) and event.get(field):
                        entry[field] = event[field]
                for url in links_for(mail, event):
                    if url not in entry["links"] and len(entry["links"]) < MAX_LINKS:
                        entry["links"].append(url)
                continue

            entry = {
                "title": event.get("title", ""),
                "date": event["date"],
                "start_time": event.get("start_time", ""),
                "end_time": event.get("end_time", ""),
                "location": event.get("location", ""),
                "kind": event.get("kind", "other"),
                "details": event.get("details", ""),
                "links": links_for(mail, event),
                "courses": mail.get("courses") or [],
                "category": mail.get("category", ""),
                "mail_id": mail.get("id"),
                "mail_subject": mail.get("subject", ""),
                "sources": [source],
                "keys": [key],
                "automatic": automatically_on(event, mail),
                "source": "mail",
                "on_calendar": is_on,
            }
            bucket[group] = entry

    # Something the student put on the calendar is not also offered as a
    # suggestion because a second mail mentioned it.
    for group in list(off):
        if group in on:
            del off[group]

    order = lambda e: (e["date"], e.get("start_time") or "99:99")
    return sorted(on.values(), key=order), sorted(off.values(), key=order)
