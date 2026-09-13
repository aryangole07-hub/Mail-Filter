#!/usr/bin/env python3
"""
calendar_store.py - what goes on the calendar, and the student's say over it.

The calendar holds only what the student cannot afford to miss: quizzes and
exams (with their portions) and things that are due (with where the
instructions are and the mail they came from). Everything else found in mail
is offered, not imposed: "Add to cal" puts it on, "Remove from cal" takes
anything off. Choices are kept in calendar_overrides.json (gitignored).

Two rules keep it honest, both learned from the real inbox:

  * Nothing is counted twice. The same quiz turns up in the announcement, in
    a reminder, and in a "marks not showing" thread weeks later - spelled
    differently ("FoFA Quiz 1", "FOFA Quiz-1", "first quiz") and, worse, dated
    with whichever mail the model happened to be reading. Mentions are merged
    by what they are - course plus quiz/assignment number, or midsem/compre
    once per course - across dates, and by wording on the same day. The merged
    entry keeps the date the mail actually announces and lists every mail.

  * Only grounded, genuine items go on by themselves. An exam must be called
    an exam (not a paper collection, not a polo shirt the model labelled
    "exam"), a deadline must read as something to submit, pay or register for,
    and the date itself must be written in the mail - or the mail must say
    "tomorrow", "this Friday" and so on. Anything that fails is still offered
    under the month, never silently dropped.
"""

import html
import json
import os
import re
from datetime import datetime

import courses

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OVERRIDES_FILE = os.path.join(SCRIPT_DIR, "calendar_overrides.json")

MUST_NOT_MISS_KINDS = {"exam", "deadline"}
MAX_LINKS = 3
MAX_TEXT_CHARS = 30000

URL_RE = re.compile(r"""https?://[^\s<>"'()\]]+""", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["'](https?://[^"']+)["']""", re.I)
TAG_RE = re.compile(r"<[^>]+>")

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

# --- is it really an exam / really due? ------------------------------------

EXAM_WORDS = re.compile(
    r"\b(quiz(?:zes)?|tests?|exam(?:ination)?s?|mid-?\s*sem(?:ester)?s?|"
    r"compre(?:hensive)?s?|end-?\s*sem(?:ester)?|vivas?|lab\s*exams?)\b", re.I)
# Paperwork around an exam, or a mail about marks - never the exam itself.
PAPER_ADMIN_RE = re.compile(
    r"\b(paper\s*(?:collection|distribution|re-?distribution|show|viewing)|"
    r"answer\s*(?:scripts?|sheets?)|scripts?\s*(?:collection|distribution)|"
    r"marks?|grades?|results?|re-?evaluation|re-?checking|"
    r"serial\s*(?:no|number)|sl\.?\s*no)\b", re.I)
DUE_WORDS = re.compile(
    r"\b(due|deadline|submi(?:t|ts|tted|tting|ssion|ssions)|assignments?|"
    r"homework|projects?|reports?|presentations?|registration|register|apply|"
    r"application|last\s+date|payments?|pay|fees?|forms?|upload|essays?|"
    r"term\s*papers?|lab\s*records?|worksheets?|problem\s*sets?|"
    r"tutorial\s*sheets?|enrol(?:l)?(?:ment)?)\b", re.I)

# "We will have our second quiz tomorrow" grounds a date without writing it.
RELATIVE_RE = re.compile(
    r"\b(today|tonight|tomorrow|day\s+after\s+tomorrow|"
    r"(?:this|next|coming)\s+(?:week|mon|tue|wed|thu|fri|sat|sun)\w*|"
    r"on\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b", re.I)

MONTH_NAMES = {
    1: ("jan", "january"), 2: ("feb", "february"), 3: ("mar", "march"),
    4: ("apr", "april"), 5: ("may",), 6: ("jun", "june"), 7: ("jul", "july"),
    8: ("aug", "august"), 9: ("sep", "sept", "september"),
    10: ("oct", "october"), 11: ("nov", "november"), 12: ("dec", "december"),
}

# --- what is it? -----------------------------------------------------------

ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4",
            "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
            "ninth": "9", "tenth": "10", "1st": "1", "2nd": "2", "3rd": "3",
            "4th": "4", "5th": "5", "6th": "6"}
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
         "vii": "7", "viii": "8", "ix": "9", "x": "10"}
THING = (r"(quiz|test|exam|viva|lab|assignment|tutorial|tut|project|"
         r"problem\s*set|homework|hw|worksheet|report|essay)")
NUMBERED_RE = re.compile(
    r"\b" + THING + r"\s*(?:no\.?|number|#)?\s*[-:#.]?\s*"
    r"(\d{1,2}|ix|iv|x|v?i{1,3}|v)\b", re.I)
ORDINAL_RE = re.compile(
    r"\b(" + "|".join(ORDINALS) + r")\s+" + THING + r"\b", re.I)
THING_ALIASES = {"tut": "tutorial", "hw": "homework"}

EXAM_TYPES = [("midsem", r"mid-?\s*sem"),
              ("compre", r"compre|comprehensive|end-?\s*sem"),
              ("viva", r"viva"), ("quiz", r"quiz"), ("test", r"\btests?\b"),
              ("exam", r"exam")]

# Words that differ between two wordings of one thing.
TITLE_NOISE = re.compile(
    r"\b(reminder|due|deadline|submission|submit|last\s+date|extended|today|"
    r"tomorrow|on|by|for|the|of|is|a|an|and|or|to|in|at|with|re|fwd|"
    r"regarding)\b", re.I)

_ALIASES = None


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def event_key(mail_id, event):
    return "{}|{}|{}".format(mail_id, event.get("date", ""),
                             " ".join((event.get("title") or "").lower().split()))


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


# ---------------------------------------------------------------------------
# Reading one mention
# ---------------------------------------------------------------------------

def mail_text(mail):
    """Everything the mail says, as plain text, for checking dates against."""
    parts = [mail.get("subject") or ""]
    for field in ("body_text", "body_html"):
        raw = (mail.get(field) or "")[:MAX_TEXT_CHARS]
        if raw:
            parts.append(html.unescape(TAG_RE.sub(" ", raw)))
    for attachment in mail.get("attachments") or []:
        if isinstance(attachment, dict) and attachment.get("text"):
            parts.append(attachment["text"][:MAX_TEXT_CHARS])
    return " ".join(parts)


def date_mentioned(text, iso_date):
    """Is this calendar date written in the text, in any common form?"""
    try:
        day = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    names = "|".join(MONTH_NAMES[day.month])
    d, mo, y = day.day, day.month, day.year
    patterns = (
        # "21st, 23rd and 25th September": a day in a list that ends in the month.
        r"(?<!\d)0?%d(?:st|nd|rd|th)?(?:\s*(?:,|&|and|to|-)\s*\d{1,2}(?:st|nd|rd|th)?){0,6}"
        r"\s*(?:of\s+)?(?:%s)\b" % (d, names),
        r"\b(?:%s)\.?\s*0?%d(?:st|nd|rd|th)?(?!\d)" % (names, d),
        r"(?<!\d)0?%d\s*[/.\-]\s*0?%d(?!\d)" % (d, mo),
        r"(?<!\d)%d\s*-\s*0?%d\s*-\s*0?%d(?!\d)" % (y, mo, d),
    )
    low = (text or "").lower()
    return any(re.search(pattern, low) for pattern in patterns)


def validated_kind(event, mail=None):
    """The event's kind, demoted to "other" when the title does not back it up."""
    kind = event.get("kind") or "other"
    title = event.get("title") or ""
    if kind == "exam":
        if EXAM_WORDS.search(title) and not PAPER_ADMIN_RE.search(title):
            return "exam"
        return "other"
    if kind == "deadline":
        if DUE_WORDS.search("{} {}".format(title, event.get("details") or "")):
            return "deadline"
        return "other"
    return kind


def automatically_on(event, mail, kind=None, grounded=True):
    """Should this go on the calendar without the student asking?"""
    kind = kind or validated_kind(event, mail)
    if kind not in MUST_NOT_MISS_KINDS or not grounded:
        return False
    if kind == "deadline" and mail.get("category") == "Fests" \
            and not mail.get("absolute"):
        return False  # a hackathon registration is an offer, not an obligation
    return True


def _alias_patterns():
    """(pattern, index, code) for every way a course is written in a title."""
    out = []
    for index, course in enumerate(courses.COURSES):
        code = course["code"]
        names = [code, code.replace(" ", ""), course.get("name", "")]
        names += list(course.get("aka") or [])
        for name in names:
            name = (name or "").strip()
            if len(name) >= 3:
                body = re.escape(name).replace(r"\ ", r"\s*")
                out.append((re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])",
                                       re.I), index, code))
        for short in course.get("short") or []:
            # Two-letter forms ("DE", "TS") are ordinary words far too often;
            # "M3" is safe because of the digit, and matched case-sensitively.
            if len(short) >= 3 or any(ch.isdigit() for ch in short):
                flags = re.I if len(short) >= 3 else 0
                out.append((re.compile(r"(?<![A-Za-z0-9])" + re.escape(short)
                                       + r"(?![A-Za-z0-9])", flags), index, code))
    out.sort(key=lambda item: len(item[0].pattern), reverse=True)
    return out


def _aliases():
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = _alias_patterns()
    return _ALIASES


def course_in_title(title):
    for pattern, _, code in _aliases():
        if pattern.search(title or ""):
            return code
    return ""


def _thing(word):
    word = " ".join((word or "").lower().split())
    return THING_ALIASES.get(word, word)


def numbered(title):
    """("quiz", "2") for "FOFA Quiz-2", "second quiz" or "Quiz II"; else None."""
    match = NUMBERED_RE.search(title or "")
    if match:
        raw = match.group(2).lower()
        number = str(int(raw)) if raw.isdigit() else ROMAN.get(raw, raw)
        return _thing(match.group(1)), number
    match = ORDINAL_RE.search(title or "")
    if match:
        return _thing(match.group(2)), ORDINALS[match.group(1).lower()]
    return None


def exam_type(title):
    low = (title or "").lower()
    for name, pattern in EXAM_TYPES:
        if re.search(pattern, low):
            return name
    return ""


def identity(title, course, kind):
    """What a mention *is*, when that can be pinned down; else None.

    Only with a known course: "Quiz 2" alone could be any course's quiz 2.
    """
    if not course or kind not in MUST_NOT_MISS_KINDS:
        return None
    if kind == "exam" and exam_type(title) in ("midsem", "compre"):
        return "{}|{}".format(course, exam_type(title))
    found = numbered(title)
    if not found:
        return None
    thing, number = found
    if kind == "exam":
        if thing not in ("quiz", "test", "exam", "viva", "lab"):
            return None
        if thing in ("test", "exam") and exam_type(title) == "quiz":
            thing = "quiz"
    return "{}|{}|{}".format(course, thing, number)


def title_tokens(title):
    """The words that make a title this thing, with wording differences removed."""
    text = title or ""
    for pattern, index, _ in _aliases():
        text = pattern.sub(" course{} ".format(chr(97 + index)), text)
    text = re.sub(r"([A-Za-z])[-#:]?(\d)", r"\1 \2", text)
    text = ORDINAL_RE.sub(lambda m: "{} {}".format(_thing(m.group(2)),
                                                  ORDINALS[m.group(1).lower()]), text)
    text = NUMBERED_RE.sub(
        lambda m: "{} {}".format(_thing(m.group(1)),
                                 str(int(m.group(2))) if m.group(2).isdigit()
                                 else ROMAN.get(m.group(2).lower(), m.group(2))), text)
    text = TITLE_NOISE.sub(" ", text.lower())
    return set(re.findall(r"[a-z0-9]+", text))


def _mention(mail, event, text):
    kind = validated_kind(event, mail)
    title = event.get("title") or ""
    course = course_in_title(title)
    mail_courses = [c for c in mail.get("courses") or [] if c]
    if not course and len(mail_courses) == 1:
        course = mail_courses[0]
    in_mail = date_mentioned(text, event["date"])
    grounded = in_mail or bool(RELATIVE_RE.search(text))
    return {
        "mail": mail,
        "event": event,
        "kind": kind,
        "title": title,
        "course": course,
        "date": event["date"],
        "key": event_key(mail.get("id"), event),
        "identity": identity(title, course, kind),
        "tokens": title_tokens(title),
        "date_in_mail": in_mail,
        "grounded": grounded,
        "automatic": automatically_on(event, mail, kind, grounded),
        "received": mail.get("received_at") or "",
    }


# ---------------------------------------------------------------------------
# Merging mentions into things
# ---------------------------------------------------------------------------

def _compatible(a, b):
    # The model labels one thing "meeting" in one mail and "other" in the next;
    # that must not keep it on the calendar twice. Only an exam or a deadline
    # has to agree on its kind, so "Quiz 2" never swallows "Quiz 2 paper
    # distribution".
    if a["kind"] != b["kind"] and (a["kind"] in MUST_NOT_MISS_KINDS
                                   or b["kind"] in MUST_NOT_MISS_KINDS):
        return False
    return not (a["course"] and b["course"] and a["course"] != b["course"])


def _similar(a, b):
    x, y = a["tokens"], b["tokens"]
    if not x or not y:
        return False
    shared = len(x & y)
    return shared / len(x | y) >= 0.6 or shared == min(len(x), len(y))


def _thread(subject):
    """"re: fwd: Handout" and "Handout" are one conversation."""
    return re.sub(r"^\s*(?:(?:re|fwd?|fw)\s*:\s*)+", "", (subject or "").lower()).strip()


def _same_thing_other_day(a, b):
    """Could these two differently-dated mentions be one thing?

    The wording must match closely (not just overlap), and a short generic
    title ("Registration") only counts when both come from the same mail or
    the same thread - two unrelated registrations are two things.
    """
    if not _compatible(a, b):
        return False
    x, y = a["tokens"], b["tokens"]
    if not x or not y or len(x & y) / len(x | y) < 0.8:
        return False
    if len(x | y) >= 3:
        return True
    return (a["mail"].get("id") == b["mail"].get("id")
            or _thread(a["mail"].get("subject")) == _thread(b["mail"].get("subject")))


def _fold_across_dates(groups):
    """Merge a thing's mentions on dates the mail never actually gives.

    A date written in a mail is kept as its own entry - that is how a real
    schedule looks (presentations on the 21st, 23rd and 25th; a vaccination
    running 13 to 18 September). A same-titled mention on a date no mail
    writes is almost always the mail's own date misread, so it is folded into
    the nearest written one. If none of the dates is written anywhere, they
    are all one uncertain thing and become one entry.
    """
    loose = [g for g in groups if not any(m["identity"] for m in g)]
    fixed = [g for g in groups if any(m["identity"] for m in g)]

    clusters = []
    for group in loose:
        for cluster in clusters:
            if any(_same_thing_other_day(a, b)
                   for other in cluster for a in other for b in group):
                cluster.append(group)
                break
        else:
            clusters.append([group])

    def written(group):
        return any(m["date_in_mail"] for m in group)

    def day(group):
        return datetime.strptime(group[0]["date"], "%Y-%m-%d").date()

    out = list(fixed)
    for cluster in clusters:
        if len(cluster) == 1:
            out.append(cluster[0])
            continue
        anchors = [g for g in cluster if written(g)]
        if not anchors:
            out.append([m for g in cluster for m in g])
            continue
        for group in anchors:
            out.append(group)
        for group in cluster:
            if group in anchors:
                continue
            nearest = min(anchors, key=lambda a: abs((day(a) - day(group)).days))
            nearest.extend(group)
    return out


def _same_day_same_thing(a, b):
    """Two mentions on the same day: one thing?"""
    if a["course"] and b["course"] and a["course"] != b["course"]:
        return False
    if a["kind"] == b["kind"] or not (a["kind"] in MUST_NOT_MISS_KINDS
                                      or b["kind"] in MUST_NOT_MISS_KINDS):
        return _similar(a, b)
    # One copy called a deadline, another a meeting. The same wording on the
    # same day is still one thing - but only near-identical wording, so "Quiz 2"
    # never swallows "Quiz 2 paper distribution".
    x, y = a["tokens"], b["tokens"]
    return bool(x and y) and len(x & y) / len(x | y) >= 0.8


def group_mentions(mentions):
    """Lists of mentions that are one real thing."""
    groups, by_identity = [], {}
    for mention in mentions:
        ident = mention["identity"]
        if ident:
            if ident not in by_identity:
                by_identity[ident] = []
                groups.append(by_identity[ident])
            by_identity[ident].append(mention)
    for mention in mentions:
        if mention["identity"]:
            continue
        home = None
        for group in groups:
            if any(other["date"] == mention["date"]
                   and _same_day_same_thing(other, mention) for other in group):
                home = group
                break
        if home is None:
            home = []
            groups.append(home)
        home.append(mention)
    return _fold_across_dates(groups)


def _strength(mention):
    """How much a mention's date is to be believed, then how recent it is."""
    event = mention["event"]
    score = 3 if mention["date_in_mail"] else (1 if mention["grounded"] else 0)
    score += 2 if event.get("start_time") else 0
    score += 1 if event.get("details") else 0
    return score, mention["received"]


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


def _most_important_kind(group, best):
    kinds = {m["kind"] for m in group}
    for kind in ("exam", "deadline"):
        if kind in kinds:
            return kind
    return best["kind"]


def merge_group(group, added, removed):
    """One calendar entry for one real thing."""
    best = max(group, key=_strength)
    same_day = [best] + [m for m in group if m is not best and m["date"] == best["date"]]
    ordered = same_day + [m for m in group if m["date"] != best["date"]]

    def first(field, pool):
        for mention in pool:
            value = mention["event"].get(field)
            if value:
                return value
        return ""

    links = []
    for mention in ordered:
        for url in links_for(mention["mail"], mention["event"]):
            if url not in links and len(links) < MAX_LINKS:
                links.append(url)

    sources, seen = [], set()
    for mention in sorted(group, key=lambda m: m["received"]):
        mail_id = mention["mail"].get("id")
        if mail_id in seen:
            continue
        seen.add(mail_id)
        sources.append({"mail_id": mail_id,
                        "subject": mention["mail"].get("subject", ""),
                        "received_at": mention["received"], "key": mention["key"]})

    keys = list(dict.fromkeys(m["key"] for m in group))
    codes = sorted({m["course"] for m in group if m["course"]}) or sorted(
        {c for m in group for c in m["mail"].get("courses") or [] if c})
    automatic = any(m["automatic"] for m in group)
    on = any(k in added for k in keys) or (
        automatic and not any(k in removed for k in keys))

    return {
        "title": best["title"],
        "date": best["date"],
        "start_time": first("start_time", same_day),
        "end_time": first("end_time", same_day),
        "location": first("location", same_day),
        "kind": _most_important_kind(group, best),
        "details": first("details", ordered),
        "links": links,
        "courses": codes,
        "category": best["mail"].get("category", ""),
        "mail_id": best["mail"].get("id"),
        "mail_subject": best["mail"].get("subject", ""),
        "sources": sources,
        "keys": keys,
        "automatic": automatic,
        "source": "mail",
        "on_calendar": on,
        # Dates other mentions carried - usually a mail's own date misread as
        # the event's. Kept for transparency, never shown as a second entry.
        "other_dates": sorted({m["date"] for m in group} - {best["date"]}),
    }


def build(mails, start_date, end_date, overrides=None, hidden_ids=None):
    """(entries on the calendar, suggestions not on it), both date-sorted.

    Mentions are merged across every stored mail before the date range is
    applied, so a quiz's wrongly-dated mention inside this month cannot survive
    just because its real date falls in the next.
    """
    overrides = overrides or load_overrides()
    added, removed = set(overrides["added"]), set(overrides["removed"])
    hidden_ids = hidden_ids or set()

    mentions = []
    for mail in mails or []:
        if not isinstance(mail, dict) or mail.get("id") in hidden_ids:
            continue
        events = [e for e in mail.get("events") or []
                  if isinstance(e, dict) and e.get("date")]
        if not events:
            continue
        text = mail_text(mail)
        for event in events:
            try:
                datetime.strptime(event["date"], "%Y-%m-%d")
            except ValueError:
                continue
            mentions.append(_mention(mail, event, text))

    on, off = [], []
    for group in group_mentions(mentions):
        entry = merge_group(group, added, removed)
        when = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        if start_date <= when <= end_date:
            (on if entry["on_calendar"] else off).append(entry)

    order = lambda e: (e["date"], e.get("start_time") or "99:99")
    return sorted(on, key=order), sorted(off, key=order)
