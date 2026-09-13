#!/usr/bin/env python3
"""
conflicts.py - does something in your mail clash with your timetable?

A makeup class, a rescheduled lab or a quiz set outside class hours can land on
top of another course's lecture. This finds those overlaps deterministically -
no model is involved - by laying every timed calendar item against the weekly
timetable in courses.py for that date.

What counts as a clash:
  * the item has a start time on a date the timetable is running;
  * its time overlaps a timetabled slot; an item with no end time is taken to
    last CLASS_MINUTES;
  * the slot belongs to a DIFFERENT course. A FoFA quiz during the FoFA lecture
    is the lecture being used for the quiz, not a clash.

Items that read as an extra / makeup / rescheduled class are flagged as such, so
the page can say "extra class clashes with …" rather than just "clashes".
"""

import re
from datetime import datetime, timedelta

import courses

CLASS_MINUTES = 50  # a BITS slot; used when an item gives no end time

EXTRA_CLASS_RE = re.compile(
    r"\b(make-?\s*up|extra\s+(?:class|lecture|tutorial|lab)|additional\s+"
    r"(?:class|lecture|tutorial|lab)|re-?scheduled|compensat\w*|"
    r"special\s+(?:class|lecture)|replacement\s+class)\b", re.I)


def _minutes(hhmm):
    try:
        hours, minutes = str(hhmm).split(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return None


def _span(start, end, default_minutes=CLASS_MINUTES):
    begin = _minutes(start)
    if begin is None:
        return None
    finish = _minutes(end) if end else None
    if finish is None or finish <= begin:
        finish = begin + default_minutes
    return begin, finish


def is_extra_class(entry):
    return bool(EXTRA_CLASS_RE.search("{} {}".format(entry.get("title") or "",
                                                     entry.get("details") or "")))


def clashes_for(entry, slots=None):
    """Timetable slots of other courses that this item overlaps."""
    span = _span(entry.get("start_time"), entry.get("end_time"))
    if not span or not entry.get("date"):
        return []
    try:
        day = datetime.strptime(entry["date"], "%Y-%m-%d").date()
    except ValueError:
        return []
    if slots is None:
        slots = courses.classes_on(day)
    own = set(entry.get("courses") or [])
    found = []
    for slot in slots:
        if slot.get("code") in own:
            continue
        slot_span = _span(slot.get("start_time"), slot.get("end_time"))
        if slot_span and span[0] < slot_span[1] and slot_span[0] < span[1]:
            found.append(slot)
    return found


def annotate(entries):
    """Add "clashes" (and "extra_class") to calendar entries, in place."""
    for entry in entries or []:
        clashes = clashes_for(entry)
        entry["extra_class"] = is_extra_class(entry)
        entry["clashes"] = [{
            "code": slot.get("code"),
            "label": courses.course_label(slot.get("code")),
            "type": slot.get("type"),
            "start_time": slot.get("start_time"),
            "end_time": slot.get("end_time"),
            "location": slot.get("location"),
        } for slot in clashes]
    return entries


def describe(entry):
    """One line for a reminder or the chat, or "" when there is no clash."""
    if not entry.get("clashes"):
        return ""
    slots = ", ".join("{} {} {}-{}".format(c["label"], c["type"].lower(),
                                           c["start_time"], c["end_time"])
                      for c in entry["clashes"])
    what = "Extra class" if entry.get("extra_class") else "This"
    when = "{} {}".format(entry.get("date", ""), entry.get("start_time", "")).strip()
    return "{} \"{}\" ({}) clashes with {}.".format(what, entry.get("title", ""),
                                                   when, slots)
