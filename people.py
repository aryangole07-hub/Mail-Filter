#!/usr/bin/env python3
"""
people.py - who teaches what, learned from your own mail.

courses.py holds the professors you told it about. This module learns the rest
by watching: when a mail names a course in its text, whoever sent it is
recorded as someone who writes about that course. After a couple of those,
their later mail can be filed under that course even when it never names it -
which is the point of "a bare Re: Handout from a lecturer".

Four things this is careful about, each learned from the real inbox:

  * a course is only *learned* from a mail whose own text named the course.
    Learning from a sender-based tag and then tagging by sender would be a
    loop that teaches itself its own guesses.
  * the LMS and Google Classroom send as no-reply@ with the professor's name
    in the display name. Keyed by address they would look like one person who
    teaches everything, so relayed mail is keyed by the person's name instead.
  * your own mail is not evidence about who teaches you. Your addresses are
    excluded.
  * "HOD" is a guess and is labelled as one. The rule of thumb - whoever mails
    the whole class about a course is usually its HOD - is exactly that, so
    mail about one tutorial or section does not count, a single mail is not
    enough, and someone who broadcasts about many courses is treated as
    administration rather than as the head of each of them.

    python people.py             # what has been learned, per course
    python people.py --sender x  # what is known about one person or address
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

import courses

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(SCRIPT_DIR, "digest_store.json")

# Mail about one section/tutorial rather than the whole course. The HOD guess
# ignores these, per the rule of thumb above.
SECTION_RE = re.compile(
    r"\b(tutorial|tut\s*[-:]?\s*\d|section\s*[-:]?\s*\d|my\s+(?:lecture|class|section|tutorial)"
    r"|our\s+(?:lecture|class|section|tutorial)|lab\s*batch|practical\s*batch"
    r"|[LT][1-9]\d?\b)\b",
    re.I,
)

# Someone saying, in their own mail, that they are the head of a department.
HOD_RE = re.compile(
    r"\b(hod|head\s+of\s+(?:the\s+)?depart?ment|head\s*,\s*depart?ment|head\s+of\s+dept)\b",
    re.I,
)

# Addresses that are a postbox, not a person: the real sender is the display
# name in front of them.
RELAY_HINTS = ("no-reply", "noreply", "donotreply", "do-not-reply", "notification")

# "Dushyant Kumar (Classroom)", "Gujji Murali Mohan Reddy (via BPHC LMS)".
RELAY_SUFFIX_RE = re.compile(
    r"\s*\((?:via\s+[^)]*|classroom|lms|bphc\s+lms|canvas|moodle|google\s+classroom)\)\s*$",
    re.I,
)

# A display name that is a machine talking, not someone's name. Without this,
# "Do not reply to this email" ends up listed as a probable head of department.
NOT_A_NAME_RE = re.compile(
    r"^(?:do\s*not\s*reply\b.*|no[\s-]*reply\b.*|noreply\b.*|automate\w*\b.*"
    r"|notification\w*\b.*|mailer[\s-]*daemon\b.*|postmaster\b.*|.*\bnotifications?)$",
    re.I,
)

# How much evidence before a sender's mail is filed under a course on the
# strength of who sent it alone.
MIN_SENDER_COURSE_MAILS = 2
MIN_SENDER_COURSE_SHARE = 0.6
# One class-wide mail is not evidence of running a department.
MIN_HOD_CLASS_MAILS = 2
# Someone "heading" more courses than this is a broadcaster, not an HOD.
MAX_HOD_COURSES = 2

MAX_HINT_SENDERS = 10
MAX_QUOTE_CHARS = 160

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def display_name(raw_from):
    """"Utkarsh Kumar" out of "Utkarsh Kumar <utkarsh.k@...>", relay tag off."""
    raw = (raw_from or "").strip()
    if "<" in raw:
        raw = raw.split("<", 1)[0]
    raw = raw.strip().strip('"').strip(" .")
    raw = RELAY_SUFFIX_RE.sub("", raw).strip()
    return "" if NOT_A_NAME_RE.match(raw) else raw


def is_relay(address):
    local = (address or "").split("@")[0].lower()
    return any(hint in local for hint in RELAY_HINTS)


def person_key(raw_from, address):
    """What counts as one person.

    A relay address is shared by every professor who posts through it, so the
    name is the identity there. Everywhere else the address is, because two
    people can share a name far less often than a no-reply box.
    """
    address = (address or "").lower().strip()
    name = display_name(raw_from)
    if address and is_relay(address) and name:
        return "name:" + " ".join(name.lower().split())
    return "addr:" + address if address else ("name:" + name.lower() if name else "")


def learn(mails, me=None):
    """person key -> what their mail shows about them."""
    mine = {a.lower() for a in (me or set()) if a}
    people = {}

    for mail in mails or []:
        if not isinstance(mail, dict):
            continue
        address = (mail.get("from_address") or "").lower().strip()
        if address and address in mine:
            continue  # your own mail says nothing about who teaches you
        key = person_key(mail.get("from"), address)
        if not key:
            continue

        record = people.setdefault(key, {
            "key": key,
            "names": Counter(),
            "addresses": Counter(),
            "mails": 0,
            "courses": Counter(),        # course named in the text of their mail
            "course_wide": Counter(),    # ...and not about one section
            "hod_quotes": [],
            "relayed": False,
            "institution": False,
            "last_seen": "",
        })

        record["mails"] += 1
        name = display_name(mail.get("from"))
        if name:
            record["names"][name] += 1
        if address:
            record["addresses"][address] += 1
        if is_relay(address):
            record["relayed"] = True
        if mail.get("institution"):
            record["institution"] = True
        received = mail.get("received_at") or ""
        if received > record["last_seen"]:
            record["last_seen"] = received

        subject = mail.get("subject") or ""
        body = mail.get("body_text") or mail.get("snippet") or ""
        # sender left out on purpose: see the module docstring.
        named = courses.tag_courses(subject=subject, body=body)
        section = bool(SECTION_RE.search(subject + "\n" + body))
        for code in named:
            record["courses"][code] += 1
            if not section:
                record["course_wide"][code] += 1

        if len(record["hod_quotes"]) < 2:
            for line in body.splitlines():
                line = " ".join(line.split())
                if line and HOD_RE.search(line):
                    record["hod_quotes"].append(line[:MAX_QUOTE_CHARS])
                    break

    return people


def load_learned(mails=None, me=None):
    if mails is None:
        try:
            with open(STORE_FILE, encoding="utf-8") as fh:
                mails = (json.load(fh) or {}).get("mails") or []
        except (OSError, json.JSONDecodeError, AttributeError):
            mails = []
    if me is None:
        try:
            import profile as profile_mod
            data = profile_mod.load()
            me = {data.get("email", ""), data.get("email_id", "")}
        except Exception:  # noqa: BLE001 - the profile is optional
            me = set()
    return learn(mails, me=me)


def name_of(record):
    if not record:
        return ""
    if record["names"]:
        return record["names"].most_common(1)[0][0]
    if record["addresses"]:
        return record["addresses"].most_common(1)[0][0]
    return record["key"].split(":", 1)[-1]


def address_of(record):
    if not record or not record["addresses"]:
        return ""
    return record["addresses"].most_common(1)[0][0]


def course_for_sender(key, learned):
    """The course this person's mail is usually about, or None.

    Requires more than one mail and a clear majority, so one stray mention of
    a course code does not permanently label someone.
    """
    record = (learned or {}).get(key)
    if not record or not record["courses"]:
        return None
    code, count = record["courses"].most_common(1)[0]
    if count < MIN_SENDER_COURSE_MAILS:
        return None
    total = sum(record["courses"].values())
    if total and count / total < MIN_SENDER_COURSE_SHARE:
        return None
    return code


def course_for_mail(mail, learned):
    """The learned course for whoever sent this mail, or None."""
    address = (mail.get("from_address") or "").lower().strip()
    return course_for_sender(person_key(mail.get("from"), address), learned)


def instructors(code, learned):
    """Everyone whose mail is about this course, most involved first."""
    found = []
    for record in (learned or {}).values():
        if not record["courses"].get(code):
            continue
        found.append({
            "key": record["key"],
            "name": name_of(record),
            "address": address_of(record),
            # A no-reply box with no human name behind it is not a candidate
            # for "who is the HOD".
            "is_person": bool(record["names"]),
            "relayed": record["relayed"],
            "mails_about_course": record["courses"][code],
            "course_wide_mails": record["course_wide"].get(code, 0),
            "says_hod": bool(record["hod_quotes"]),
            "last_seen": record["last_seen"],
        })
    found.sort(key=lambda p: (p["course_wide_mails"], p["mails_about_course"]),
               reverse=True)
    return found


def _raw_hod_guess(code, learned):
    people = instructors(code, learned)
    if not people:
        return None

    people = [p for p in people if p["is_person"]]
    if not people:
        return None

    stated = [p for p in people if p["says_hod"]]
    if stated:
        best = stated[0]
        return {
            "key": best["key"], "name": best["name"], "address": best["address"],
            "basis": "stated",
            "why": "their own mail calls them the head of the department",
        }

    strong = [p for p in people if p["course_wide_mails"] >= MIN_HOD_CLASS_MAILS]
    if not strong:
        return None
    best = strong[0]
    return {
        "key": best["key"], "name": best["name"], "address": best["address"],
        "basis": "inferred",
        "why": "they sent {} mails to the whole class about {}, more than "
               "anyone else".format(best["course_wide_mails"],
                                    courses.course_label(code)),
    }


def hod_guesses(learned):
    """code -> guess, with broadcasters removed.

    Someone who comes out top for several courses is the LMS, the department
    office or a coordinator coming through one address - not the head of every
    one of those departments. Dropping them is better than asserting four
    contradictory HODs.
    """
    raw = {}
    for course in courses.COURSES:
        guess = _raw_hod_guess(course["code"], learned)
        if guess:
            raw[course["code"]] = guess

    counted = Counter(g["key"] for g in raw.values() if g["basis"] != "stated")
    broadcasters = {key for key, n in counted.items() if n > MAX_HOD_COURSES}
    return {code: guess for code, guess in raw.items()
            if guess["key"] not in broadcasters}


def hod_guess(code, learned):
    return hod_guesses(learned).get(code)


def classifier_hint(learned):
    """Lines for the classifier prompt: who writes about what.

    This is what makes the next mail from a lecturer land under their course
    even when the subject is just "Re: Handout".
    """
    pairs = []
    for record in (learned or {}).values():
        code = course_for_sender(record["key"], learned)
        if code:
            pairs.append((record["courses"][code], name_of(record),
                          address_of(record), code))
    if not pairs:
        return ""
    pairs.sort(reverse=True)
    lines = []
    for _, name, address, code in pairs[:MAX_HINT_SENDERS]:
        who = "{} <{}>".format(name, address) if address else name
        lines.append("- {} writes about {} ({})".format(
            who, courses.course_label(code), code))
    return ("Senders whose mail has been about one course before. Unless this "
            "particular mail is clearly about something else, it is about that "
            "same course again:\n" + "\n".join(lines) + "\n\n")


def facts_block(learned):
    """Per-course people, for the chat prompt. Guesses are labelled as such."""
    guesses = hod_guesses(learned)
    lines = []
    for course in courses.COURSES:
        code = course["code"]
        people = instructors(code, learned)
        registry = [p for p in course.get("profs", []) if p and "@" not in p]
        if not people and not registry:
            continue

        label = "{} ({}) - {}".format(courses.course_label(code), code,
                                      course["name"])
        if registry:
            lines.append("- {}: taught by {}".format(label, ", ".join(registry)))
        else:
            lines.append("- {}".format(label))
        seen = ["{}{}, {} mail(s)".format(
            person["name"], " (via a no-reply address)" if person["relayed"] else "",
            person["mails_about_course"]) for person in people[:4]]
        if seen:
            lines.append("  mail about it has come from: " + "; ".join(seen))
        guess = guesses.get(code)
        if guess:
            if guess["basis"] == "stated":
                lines.append("  {} is the head of department (their own mail "
                             "says so).".format(guess["name"]))
            else:
                lines.append("  {} is probably the head of department: {}. "
                             "Say \"probably\" if you pass this on - it is "
                             "inferred, not stated.".format(guess["name"],
                                                            guess["why"]))
    if not lines:
        return ""
    return "Who teaches what, from your own mail:\n" + "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Who teaches what, learned from mail.")
    parser.add_argument("--sender", help="A name or address to look up.")
    args = parser.parse_args()

    learned = load_learned()
    if not learned:
        print("Nothing learned yet - run the digest first.")
        return 0

    if args.sender:
        needle = args.sender.lower()
        matches = [r for r in learned.values()
                   if needle in r["key"].lower()
                   or any(needle in a for a in r["addresses"])
                   or any(needle in n.lower() for n in r["names"])]
        if not matches:
            print("Nothing from {} in the store.".format(args.sender))
            return 1
        for record in matches:
            print("{} <{}>{}".format(name_of(record), address_of(record) or "-",
                                     " [relayed]" if record["relayed"] else ""))
            print("  mails         : {}".format(record["mails"]))
            print("  courses named : {}".format(dict(record["courses"]) or "-"))
            print("  class-wide    : {}".format(dict(record["course_wide"]) or "-"))
            print("  filed under   : {}".format(
                course_for_sender(record["key"], learned) or "-"))
            for quote in record["hod_quotes"]:
                print("  says          : {}".format(quote))
            print()
        return 0

    print(facts_block(learned) or "No course people learned yet.")
    print()
    print("--- what the classifier is told ---")
    print(classifier_hint(learned) or "(nothing yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
