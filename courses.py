#!/usr/bin/env python3
"""
courses.py - the course registry, the weekly timetable, and mail tagging.

Everything semester-specific lives here so mail_filter.py and viewer.py stay
general. Source of truth is the student's own timetable
(week of Mon 7 Sep 2026), transcribed day by day.

Adding a professor: put their name or address in the course's "profs" list -
mail from them is tagged with that course even when the subject never names
it. That is what catches a bare "Re: Handout" from a lecturer.
"""

import re
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

COURSES = [
    {
        "code": "ECON F211",
        "name": "Principles of Economics",
        "short": ["POE"],
        "profs": ["Mini Thomas", "Mini Thomas P", "Rishi Kumar"],
    },
    {
        "code": "ECON F212",
        "name": "Fundamentals of Finance and Accounting",
        "short": ["FoFA", "FOFA"],
        "aka": ["Funda of Fin and Account", "Fundamentals of Finance & Accounting"],
        "profs": ["Utkarsh Kumar", "Shobhana Sikhawal", "utkarsh.k"],
    },
    {
        # The student first gave this as "M3 (MATH F211) Mathematics III", but
        # the timetable has no MATH F211 - it has MATH F201 Differential
        # Equations in the slot. Both codes are registered so neither spelling
        # can go untagged; the discrepancy is flagged in the README.
        "code": "MATH F201",
        "name": "Differential Equations",
        "short": ["M3", "DE"],
        "aka": ["Mathematics III", "Maths 3", "MATH F211", "Differential Equation"],
        "profs": ["Jagan Mohan Jonnalagadda", "Gujji Murali Mohan Reddy"],
    },
    {
        "code": "ECON F213",
        "name": "Mathematical and Statistical Methods",
        "short": ["MSN", "MSM"],
        "aka": ["Mathematical & Statistical Method", "Mathematics & Statistics"],
        "profs": ["Dushyant Kumar"],
    },
    {
        "code": "ECON F214",
        "name": "Economic Environment of Business",
        "short": ["EEB"],
        "aka": ["Economic Env of Business"],
        "profs": ["Sudatta Banerjee", "Mona Mariam Alexander"],
    },
    {
        "code": "HSS F352",
        "name": "Technology, Work and Society",
        "short": ["TWS", "TS"],
        "aka": ["Technological Sciences", "Technology Work and Society"],
        "profs": ["Ufaque Paiker", "ufaque.paiker"],
    },
    {
        "code": "BITS F225",
        "name": "Environmental Studies",
        "short": ["EVS"],
        "aka": ["Environmental Sciences", "Environmental Science"],
        "profs": ["Shuvadeep Maity"],
    },
    {
        "code": "HSS F222",
        "name": "Linguistics",
        "short": [],
        "profs": ["Pranesh Bhargava"],
    },
]

BY_CODE = {c["code"]: c for c in COURSES}

# ---------------------------------------------------------------------------
# The weekly timetable
# ---------------------------------------------------------------------------
# Transcribed from the day-by-day timetable, which is authoritative. The
# summary section of that same document says ECON F211 runs Tue/Wed/Thu at
# 15:00, but its Wednesday entry puts ECON F211 at 17:00 - the day-by-day
# entry is used, and the conflict is flagged in the README.
#
# day: Monday = 0 ... Sunday = 6.

WEEKLY = [
    # Monday
    {"day": 0, "start": "09:00", "end": "09:50", "code": "ECON F212", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    {"day": 0, "start": "10:00", "end": "10:50", "code": "HSS F222", "type": "Lecture", "section": "L1", "room": "J Block J217"},
    {"day": 0, "start": "14:00", "end": "14:50", "code": "HSS F352", "type": "Lecture", "section": "L1", "room": "J Block J119"},
    {"day": 0, "start": "16:00", "end": "16:50", "code": "MATH F201", "type": "Lecture", "section": "L2", "room": "F Block F105"},
    {"day": 0, "start": "17:00", "end": "17:50", "code": "ECON F214", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    # Tuesday
    {"day": 1, "start": "08:00", "end": "08:50", "code": "BITS F225", "type": "Lecture", "section": "L1", "room": "F Block F105"},
    {"day": 1, "start": "10:00", "end": "10:50", "code": "ECON F214", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    {"day": 1, "start": "11:00", "end": "11:50", "code": "ECON F213", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    {"day": 1, "start": "15:00", "end": "15:50", "code": "ECON F211", "type": "Lecture", "section": "L3", "room": "F Block F104"},
    {"day": 1, "start": "16:00", "end": "16:50", "code": "ECON F213", "type": "Tutorial", "section": "T1", "room": "F Block F207"},
    {"day": 1, "start": "17:00", "end": "17:50", "code": "MATH F201", "type": "Tutorial", "section": "T1", "room": "G Block G107"},
    # Wednesday
    {"day": 2, "start": "09:00", "end": "09:50", "code": "ECON F212", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    {"day": 2, "start": "10:00", "end": "10:50", "code": "HSS F222", "type": "Lecture", "section": "L1", "room": "J Block J217"},
    {"day": 2, "start": "14:00", "end": "14:50", "code": "HSS F352", "type": "Lecture", "section": "L1", "room": "J Block J119"},
    {"day": 2, "start": "16:00", "end": "16:50", "code": "MATH F201", "type": "Lecture", "section": "L2", "room": "F Block F105"},
    {"day": 2, "start": "17:00", "end": "17:50", "code": "ECON F211", "type": "Lecture", "section": "L3", "room": "F Block F104"},
    # Thursday
    {"day": 3, "start": "08:00", "end": "08:50", "code": "BITS F225", "type": "Lecture", "section": "L1", "room": "F Block F105"},
    {"day": 3, "start": "10:00", "end": "10:50", "code": "ECON F214", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    {"day": 3, "start": "11:00", "end": "11:50", "code": "ECON F213", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    {"day": 3, "start": "15:00", "end": "15:50", "code": "ECON F211", "type": "Lecture", "section": "L3", "room": "F Block F104"},
    {"day": 3, "start": "16:00", "end": "16:50", "code": "ECON F214", "type": "Tutorial", "section": "T1", "room": "F Block F207"},
    # Friday
    {"day": 4, "start": "09:00", "end": "09:50", "code": "ECON F212", "type": "Lecture", "section": "L1", "room": "F Block F207"},
    {"day": 4, "start": "10:00", "end": "10:50", "code": "HSS F222", "type": "Lecture", "section": "L1", "room": "J Block J217"},
    {"day": 4, "start": "12:00", "end": "12:50", "code": "BITS F225", "type": "Lecture", "section": "L1", "room": "F Block F105"},
    {"day": 4, "start": "14:00", "end": "14:50", "code": "HSS F352", "type": "Lecture", "section": "L1", "room": "J Block J119"},
    {"day": 4, "start": "16:00", "end": "16:50", "code": "MATH F201", "type": "Lecture", "section": "L2", "room": "F Block F105"},
    {"day": 4, "start": "17:00", "end": "17:50", "code": "ECON F213", "type": "Lecture", "section": "L1", "room": "F Block F207"},
]

# Who actually teaches each slot. Lectures and tutorials differ for three
# courses, so this is keyed on (code, type) rather than on the course alone.
SLOT_PROFS = {
    ("ECON F211", "Lecture"): ["Mini Thomas P", "Rishi Kumar"],
    ("ECON F212", "Lecture"): ["Utkarsh Kumar", "Shobhana Sikhawal"],
    ("ECON F213", "Lecture"): ["Dushyant Kumar"],
    ("ECON F213", "Tutorial"): ["Dushyant Kumar"],
    ("ECON F214", "Lecture"): ["Sudatta Banerjee"],
    ("ECON F214", "Tutorial"): ["Mona Mariam Alexander"],
    ("MATH F201", "Lecture"): ["Jagan Mohan Jonnalagadda"],
    ("MATH F201", "Tutorial"): ["Gujji Murali Mohan Reddy"],
    ("BITS F225", "Lecture"): ["Shuvadeep Maity"],
    ("HSS F222", "Lecture"): ["Pranesh Bhargava"],
    ("HSS F352", "Lecture"): ["Ufaque Paiker"],
}

# The timetable is a weekly pattern, not a calendar. It is only projected onto
# dates inside the semester; outside it, the calendar shows mail events only.
TERM_START = date(2026, 8, 1)
TERM_END = date(2026, 12, 31)

# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------

# An abbreviation this short matches ordinary prose constantly ("ts", "m3").
# Below this length it is only honoured in a subject line, or in a body when
# written in capitals exactly as a course name would be.
SHORT_FORM_MIN_SAFE_LEN = 4


def _code_pattern(code):
    """ECON F211 -> matches "ECON F211", "ECONF211", "econ  f211", "ECON-F211"."""
    head, _, tail = code.partition(" ")
    return re.compile(
        r"\b" + re.escape(head) + r"[\s\-_]*" + re.escape(tail) + r"\b",
        re.IGNORECASE,
    )


def _word_pattern(word, flags=re.IGNORECASE):
    return re.compile(r"\b" + re.escape(word) + r"\b", flags)


def _compile():
    out = []
    for course in COURSES:
        akas = list(course.get("aka", []))
        aka_res = []
        for alias in akas:
            # An alias that is itself a course code needs code-style matching.
            aka_res.append(_code_pattern(alias) if re.match(r"^[A-Z]+\s+F\d+$", alias)
                           else _word_pattern(alias))
        out.append({
            "code": course["code"],
            "code_re": _code_pattern(course["code"]),
            "name_re": _word_pattern(course["name"]),
            "aka_res": aka_res,
            "short_any": [_word_pattern(s) for s in course.get("short", [])
                          if len(s) >= SHORT_FORM_MIN_SAFE_LEN],
            "short_strict": [(s, _word_pattern(s, 0)) for s in course.get("short", [])
                             if len(s) < SHORT_FORM_MIN_SAFE_LEN],
        })
    return out


_COMPILED = _compile()


def _hits(entry, subject, body):
    """True if this course is mentioned. See SHORT_FORM_MIN_SAFE_LEN."""
    both = subject + "\n" + body

    if entry["code_re"].search(both):
        return True
    if entry["name_re"].search(both):
        return True
    if any(r.search(both) for r in entry["aka_res"]):
        return True
    if any(r.search(both) for r in entry["short_any"]):
        return True

    # Risky two- and three-letter forms: anywhere in the subject (where a
    # student writes "M3 quiz"), but in the body only in capitals, so the "ts"
    # inside "results" or a sentence ending "... m3." does not count.
    for _, pattern in entry["short_strict"]:
        if pattern.search(subject) or pattern.search(subject.upper()):
            return True
    for text, pattern in entry["short_strict"]:
        if pattern.search(body) and text.upper() == text:
            return True
    return False


def _prof_hit(course, sender):
    sender_l = (sender or "").lower()
    return any(p.lower() in sender_l for p in course.get("profs", []) if p)


def tag_courses(subject="", body="", sender=""):
    """Course codes this mail relates to.

    Returns a list such as ["ECON F212"]. Empty means "no course identified",
    which is normal - plenty of campus mail is administrative.
    """
    subject = subject or ""
    body = body or ""
    found = []
    for entry in _COMPILED:
        course = BY_CODE[entry["code"]]
        if _hits(entry, subject, body) or _prof_hit(course, sender):
            found.append(entry["code"])
    return found


def course_label(code):
    """Short display label, e.g. "FoFA", falling back to the course name."""
    course = BY_CODE.get(code)
    if not course:
        return code
    shorts = course.get("short") or []
    return shorts[0] if shorts else course["name"]


def registry_for_ui():
    return [
        {
            "code": c["code"],
            "name": c["name"],
            "label": course_label(c["code"]),
            "short": c.get("short", []),
            "profs": c.get("profs", []),
        }
        for c in COURSES
    ]


# ---------------------------------------------------------------------------
# Timetable -> calendar entries
# ---------------------------------------------------------------------------

def classes_on(day):
    """The timetable entries for one date, as calendar-shaped dicts.

    Returns [] outside the semester and at weekends, so the calendar does not
    invent lectures during the winter break.
    """
    if isinstance(day, datetime):
        day = day.date()
    if not (TERM_START <= day <= TERM_END):
        return []

    out = []
    for slot in WEEKLY:
        if slot["day"] != day.weekday():
            continue
        course = BY_CODE.get(slot["code"], {})
        out.append({
            "title": "{} {}".format(course_label(slot["code"]), slot["type"]),
            "date": day.strftime("%Y-%m-%d"),
            "start_time": slot["start"],
            "end_time": slot["end"],
            "location": slot["room"],
            "kind": "class",
            "code": slot["code"],
            "course_name": course.get("name", ""),
            "section": slot["section"],
            "type": slot["type"],
            "profs": SLOT_PROFS.get((slot["code"], slot["type"]), []),
            "source": "timetable",
        })
    out.sort(key=lambda e: e["start_time"])
    return out


def classes_between(start, end):
    """Timetable entries for every date in [start, end], inclusive."""
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    out = []
    day = start
    while day <= end:
        out.extend(classes_on(day))
        day += timedelta(days=1)
    return out


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]


def timetable_block(today=None):
    """The weekly timetable as prose, for the chat prompt.

    The chat is asked "when is my next lecture" and "who takes my tutorial",
    and neither is in any email - it is in here. Written out day by day with
    the room and who takes the slot, and with today named so "next" means
    something.
    """
    lines = []
    if today is not None:
        if isinstance(today, datetime):
            today = today.date()
        lines.append("Today is {} ({}).".format(
            today.strftime("%d %b %Y"), DAY_NAMES[today.weekday()]))
        if not (TERM_START <= today <= TERM_END):
            lines.append("This date is outside the semester, so the weekly "
                         "timetable below is not running.")

    for day in range(5):
        slots = [s for s in WEEKLY if s["day"] == day]
        if not slots:
            continue
        slots.sort(key=lambda s: s["start"])
        rendered = []
        for slot in slots:
            profs = SLOT_PROFS.get((slot["code"], slot["type"]), [])
            rendered.append("{}-{} {} {} {} ({}){}".format(
                slot["start"], slot["end"], course_label(slot["code"]),
                slot["code"], slot["type"], slot["room"],
                " with " + ", ".join(profs) if profs else ""))
        lines.append("- {}: {}".format(DAY_NAMES[day], "; ".join(rendered)))

    if not lines:
        return ""
    return ("Your weekly timetable (every week of the semester, not a one-off):\n"
            + "\n".join(lines))
