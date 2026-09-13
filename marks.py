#!/usr/bin/env python3
"""
marks.py - marks and grades from your mail, and a CGPA simulator.

Released marks are read without a model, from three places:

  * sentences in mail that is about marks - "Midsem: 34/40", "Quiz 1 marks: 8",
    "You have scored 17.5/20 in the assignment";
  * rows of attached mark sheets that contain your ID (attachments.py already
    pulled those out with their column names) - a column called "Quiz 1 (10)"
    or "Midsem marks" with a number in it;
  * marks you type in yourself.

Only numbers the mail actually states as a score are recorded. A number counts
only when the mail talks about marks at all, and when it is clearly a score -
it has a maximum ("8/10", "8 out of 10") or sits right after "marks"/"score".
That keeps "Assignment 2: 25 September" and "Total: 500 rupees" out. A score
without a stated maximum is kept with max unknown, never guessed.

The same component for the same course is one entry: the most recently received
mail wins, and a mark you entered yourself always wins.

The simulator uses BITS Pilani grade points (A 10, A- 9, B 8, B- 7, C 6, C- 5,
D 4, E 2, NC 0) and course units you set: pick an expected grade per course to
see the semester GPA and the resulting CGPA, or ask what average a target needs.

Stored in marks.json (gitignored).
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone

import calendar_store
import courses

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MARKS_FILE = os.path.join(SCRIPT_DIR, "marks.json")

GRADE_POINTS = {"A": 10, "A-": 9, "B": 8, "B-": 7, "C": 6, "C-": 5,
                "D": 4, "E": 2, "NC": 0}
DEFAULT_UNITS = 3

COMPONENT = (r"(?:mid[-\s]?sem(?:ester)?(?:\s+exam(?:ination)?)?|compre(?:hensive)?"
             r"(?:\s+exam(?:ination)?)?|end[-\s]?sem|quiz(?:zes)?\s*[-#]?\s*\d{0,2}|"
             r"class\s+test\s*\d{0,2}|surprise\s+test\s*\d{0,2}|test\s*[-#]?\s*\d{1,2}|"
             r"assignment\s*[-#]?\s*\d{0,2}|lab(?:\s+exam|\s+test)?\s*\d{0,2}|"
             r"tutorial\s*(?:test\s*)?\d{0,2}|project|viva|presentation|"
             r"ec\s*[-#]?\s*\d|evaluative\s+component\s*\d?|total|overall)")
NUMBER = r"(\d{1,3}(?:\.\d{1,2})?)"
OUT_OF = r"\s*(?:/|out\s+of)\s*"

# "Midsem: 34/40", "Quiz 1 - 8 out of 10" - a score with its maximum.
WITH_MAX_RE = re.compile(
    r"\b(" + COMPONENT + r")\b(?:\s+marks?|\s+score)?\s*[:=\-–]?\s*" + NUMBER + OUT_OF + NUMBER,
    re.I)
# "Quiz 2 marks: 7.5" - no maximum, but explicitly called marks or a score.
MARKS_WORD_RE = re.compile(
    r"\b(" + COMPONENT + r")\b\s+(?:marks?|score)\s*[:=\-–]?\s*" + NUMBER + r"(?![\d/])",
    re.I)
# "You have scored 17.5/20 in the assignment", "got 8 out of 10 in quiz 1"
SCORED_RE = re.compile(
    r"\b(?:scored|secured|got|obtained|received)\s+" + NUMBER +
    r"(?:\s*marks?)?(?:" + OUT_OF + NUMBER + r")?(?:\s*marks?)?\s+(?:in|for)\s+"
    r"(?:the\s+|your\s+)?(" + COMPONENT + r")\b", re.I)
# The mail has to be about marks at all before its numbers are read as marks.
MARKS_CONTEXT_RE = re.compile(
    r"\b(marks?|scores?|scored|grades?|graded|results?|evaluat\w*|cgpa|sgpa|"
    r"answer\s*scripts?|marksheet)\b", re.I)
# "Quiz 1 (10)", "Midsem [40]", "Assignment 2 - Max 20" in a column name
HEADER_MAX_RE = re.compile(r"[\(\[]\s*(?:max\.?\s*)?(\d{1,3})\s*[\)\]]|max\.?\s*(\d{1,3})", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load():
    try:
        with open(MARKS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    cgpa = data.get("cgpa_so_far")
    units = data.get("units_so_far")
    return {
        "manual": [m for m in data.get("manual") or [] if isinstance(m, dict) and m.get("id")],
        "units": {k: v for k, v in (data.get("units") or {}).items()
                  if isinstance(v, (int, float)) and 0 < v <= 20},
        "expected": {k: v for k, v in (data.get("expected") or {}).items()
                     if v in GRADE_POINTS},
        "hidden": [k for k in data.get("hidden") or [] if isinstance(k, str)],
        "cgpa_so_far": cgpa if isinstance(cgpa, (int, float)) else None,
        "units_so_far": units if isinstance(units, (int, float)) else None,
    }


def _save(data):
    tmp = MARKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, MARKS_FILE)


# ---------------------------------------------------------------------------
# Reading marks out of mail
# ---------------------------------------------------------------------------

def normalise_component(text):
    """"Mid-Sem Exam" -> "Midsem", "quiz-2" -> "Quiz 2", "EC 3" -> "EC 3"."""
    low = " ".join((text or "").lower().replace("-", " ").replace("#", " ").split())
    number = re.search(r"(\d{1,2})\s*$", low)
    num = " " + number.group(1) if number else ""
    if low.startswith("mid"):
        return "Midsem"
    if low.startswith(("compre", "end")):
        return "Compre"
    for word, label in (("quiz", "Quiz"), ("class test", "Class test"),
                        ("surprise test", "Surprise test"), ("test", "Test"),
                        ("assignment", "Assignment"), ("lab", "Lab"),
                        ("tutorial", "Tutorial"), ("project", "Project"),
                        ("viva", "Viva"), ("presentation", "Presentation"),
                        ("evaluative component", "EC"), ("ec", "EC"),
                        ("total", "Total"), ("overall", "Total")):
        if low.startswith(word):
            return (label + num).strip()
    return low.title()


def _entry(course, component, score, maximum, mail, evidence, source):
    score, maximum = _number(score), _number(maximum)
    if score is None:
        return None
    if maximum is not None and (maximum <= 0 or score > maximum):
        return None  # "34/20" is a misreading, not a mark
    label = normalise_component(component)
    if label == "Total" and maximum is None:
        return None  # "Total: 500" is far more often money than marks
    return {
        "course": course or "",
        "component": label,
        "score": score,
        "max": maximum,
        "pct": round(100.0 * score / maximum, 1) if maximum else None,
        "mail_id": mail.get("id"),
        "subject": mail.get("subject", ""),
        "received_at": mail.get("received_at", ""),
        "evidence": " ".join(evidence.split())[:200],
        "source": source,
    }


def _course_for(sentence, mail):
    course = (calendar_store.course_in_title(sentence)
              or calendar_store.course_in_title(mail.get("subject") or ""))
    if not course:
        tags = [c for c in mail.get("courses") or [] if c]
        course = tags[0] if len(tags) == 1 else ""
    return course


def marks_in_mail(mail):
    """Every mark a single mail states, from its text and its attached sheets."""
    found = []
    text = " ".join([mail.get("subject") or "",
                     mail.get("body_text") or TAG_RE.sub(" ", mail.get("body_html") or "")])
    if MARKS_CONTEXT_RE.search(text):
        for sentence in re.split(r"(?<=[.!?\n])\s+", text[:30000]):
            spans = []
            for regex, groups in ((WITH_MAX_RE, (1, 2, 3)), (SCORED_RE, (3, 1, 2)),
                                  (MARKS_WORD_RE, (1, 2, None))):
                for match in regex.finditer(sentence):
                    if any(match.start() < end and start < match.end() for start, end in spans):
                        continue  # the same words already read as a mark
                    comp, score, cap = groups
                    entry = _entry(_course_for(sentence, mail), match.group(comp),
                                   match.group(score), match.group(cap) if cap else None,
                                   mail, sentence, "mail")
                    if entry:
                        spans.append((match.start(), match.end()))
                        found.append(entry)

    for attachment in mail.get("attachments") or []:
        for row in (attachment or {}).get("matches") or []:
            for header, value in (row.get("cells") or {}).items():
                if not re.fullmatch(r"\s*" + NUMBER + r"\s*", str(value)):
                    continue
                if not re.search(r"\b" + COMPONENT + r"\b|\bmarks?\b|\bscore\b", header, re.I):
                    continue
                label = re.search(COMPONENT, header, re.I)
                cap = HEADER_MAX_RE.search(header)
                maximum = (cap.group(1) or cap.group(2)) if cap else None
                course = (calendar_store.course_in_title(attachment.get("filename") or "")
                          or _course_for(header, mail))
                entry = _entry(course, label.group(0) if label else header, value, maximum,
                               mail, "{} - {}: {}".format(attachment.get("filename", ""),
                                                          header, value), "attachment")
                if entry:
                    found.append(entry)
    return found


def _mark_key(entry):
    return "{}|{}".format(entry.get("course") or "?", (entry.get("component") or "").lower())


def build(mails, hidden_ids=None):
    """Everything the Marks tab shows: marks, per-course rows, simulator."""
    hidden_ids = hidden_ids or set()
    data = load()
    best = {}
    for mail in mails or []:
        if not isinstance(mail, dict) or mail.get("id") in hidden_ids:
            continue
        for entry in marks_in_mail(mail):
            key = _mark_key(entry)
            if key not in best or entry["received_at"] > best[key]["received_at"]:
                entry["id"] = "mail-" + re.sub(r"[^a-z0-9|]+", "-", key.lower())
                best[key] = entry
    for manual in data["manual"]:
        entry = dict(manual, source="you",
                     pct=round(100.0 * manual["score"] / manual["max"], 1)
                     if manual.get("max") else None)
        best[_mark_key(entry)] = entry

    marks = [m for m in best.values() if m["id"] not in data["hidden"]]
    marks.sort(key=lambda m: (courses.course_label(m["course"]) if m["course"] else "~",
                              m["component"]))

    course_rows = []
    for course in courses.COURSES:
        code = course["code"]
        mine = [m for m in marks if m["course"] == code]
        scored = [m for m in mine if m.get("max")]
        course_rows.append({
            "code": code,
            "label": courses.course_label(code),
            "name": course.get("name", ""),
            "units": data["units"].get(code, DEFAULT_UNITS),
            "expected": data["expected"].get(code, ""),
            "marks": len(mine),
            "scored": sum(m["score"] for m in scored),
            "out_of": sum(m["max"] for m in scored),
        })
    return {
        "marks": marks,
        "courses": course_rows,
        "history": {"cgpa_so_far": data["cgpa_so_far"], "units_so_far": data["units_so_far"]},
        "simulator": simulate(course_rows, data["cgpa_so_far"], data["units_so_far"]),
        "grade_points": GRADE_POINTS,
    }


# ---------------------------------------------------------------------------
# Your own marks, units, expected grades and CGPA so far
# ---------------------------------------------------------------------------

def add_mark(course, component, score, maximum=None):
    score = _number(score)
    maximum = _number(maximum) if maximum not in (None, "") else None
    component = " ".join((component or "").split())[:60]
    if score is None or not component:
        return None
    if maximum is not None and (maximum <= 0 or score > maximum):
        return None
    if course and course not in courses.BY_CODE:
        return None
    data = load()
    entry = {"id": "you-" + uuid.uuid4().hex[:10], "course": course or "",
             "component": normalise_component(component), "score": score, "max": maximum,
             "mail_id": None, "subject": "", "received_at": _now(), "evidence": "",
             "source": "you"}
    data["manual"] = [m for m in data["manual"] if _mark_key(m) != _mark_key(entry)]
    data["manual"].append(entry)
    _save(data)
    return entry


def remove_mark(mark_id):
    """Delete your own mark, or hide one read from mail so it stays gone."""
    data = load()
    before = len(data["manual"])
    data["manual"] = [m for m in data["manual"] if m.get("id") != mark_id]
    if len(data["manual"]) == before:
        if not mark_id.startswith("mail-"):
            return False
        if mark_id not in data["hidden"]:
            data["hidden"].append(mark_id)
    _save(data)
    return True


def set_course(code, units=None, expected=None):
    if code not in courses.BY_CODE:
        return False
    data = load()
    if units is not None:
        value = _number(units)
        if value is None or not 0 < value <= 20:
            return False
        data["units"][code] = value
    if expected is not None:
        if expected == "":
            data["expected"].pop(code, None)
        elif expected in GRADE_POINTS:
            data["expected"][code] = expected
        else:
            return False
    _save(data)
    return True


def set_history(cgpa_so_far=None, units_so_far=None):
    cgpa = _number(cgpa_so_far) if cgpa_so_far not in (None, "") else None
    units = _number(units_so_far) if units_so_far not in (None, "") else None
    if cgpa_so_far not in (None, "") and (cgpa is None or not 0 <= cgpa <= 10):
        return False
    if units_so_far not in (None, "") and (units is None or units < 0):
        return False
    data = load()
    data["cgpa_so_far"] = cgpa
    data["units_so_far"] = units
    _save(data)
    return True


# ---------------------------------------------------------------------------
# CGPA simulator
# ---------------------------------------------------------------------------

def simulate(course_rows, cgpa_so_far=None, units_so_far=None, target=None):
    """Semester GPA and new CGPA from expected grades; what a target needs."""
    graded = [c for c in course_rows if c.get("expected") in GRADE_POINTS]
    sem_units = sum(c["units"] for c in graded)
    sem_points = sum(GRADE_POINTS[c["expected"]] * c["units"] for c in graded)
    sem_gpa = round(sem_points / sem_units, 2) if sem_units else None

    prior_units = units_so_far or 0
    prior_points = (cgpa_so_far or 0) * prior_units
    if sem_units and cgpa_so_far is not None and prior_units:
        new_cgpa = round((prior_points + sem_points) / (prior_units + sem_units), 2)
    else:
        new_cgpa = sem_gpa

    result = {"graded_courses": len(graded), "semester_units": sem_units,
              "semester_gpa": sem_gpa, "new_cgpa": new_cgpa}
    if target is not None:
        all_units = sum(c["units"] for c in course_rows)
        needed = None
        if all_units:
            needed = round((target * (prior_units + all_units) - prior_points) / all_units, 2)
        result.update(target=target, average_points_needed=needed,
                      target_reachable=needed is not None and needed <= 10)
    return result
