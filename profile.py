#!/usr/bin/env python3
"""
profile.py - who this student is.

The chat can only answer "which room is my exam in" if it knows which ID to
look for in the seating sheet, and "when is my next lecture" if it knows whose
timetable it is holding. That identity lives here, in profile.json, and
nowhere else - no other module hard-codes a name or an ID.

Nothing is invented: unknown fields stay empty, and the chat is told to say it
does not know rather than guess. What can be read off your own mail is filled
in for you:

    python profile.py                 # show what is known
    python profile.py --detect        # fill blanks from digest_store.json
    python profile.py --set hostel="Krishna Bhavan" --set room=412

profile.json holds your name, ID and room, so it is gitignored like the mail
store. It never leaves this machine.
"""

import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(SCRIPT_DIR, "profile.json")
STORE_FILE = os.path.join(SCRIPT_DIR, "digest_store.json")

# A BITS campus ID: 2025B3PS0420H. This is the one that appears in seating
# plans and mark sheets, and it is not the same string as the mail login.
CAMPUS_ID_RE = re.compile(r"\b(20\d\d[A-Z]\d[A-Z]{2}\d{4}[A-Z])\b")
MAIL_ID_RE = re.compile(r"\b([fhpd]20\d{6})\b", re.I)

FIELDS = {
    "name": "Full name, as the college writes it",
    "campus_id": "Campus ID, e.g. 2025B3PS0420H - what seating plans use",
    "email_id": "Mail login, e.g. f20250420",
    "email": "College address",
    "campus": "Campus",
    "batch": "Year of joining",
    "programme": "Degree and branch, if you want it known",
    "hostel": "Hostel / bhavan",
    "room": "Hostel room",
    "phone": "Contact number, if you want it known",
    "notes": "Anything else the chat should know about you",
}

DEFAULTS = {
    "name": "",
    "campus_id": "",
    "email_id": "",
    "email": "",
    "campus": "BITS Pilani, Hyderabad Campus",
    "batch": "",
    "programme": "",
    "hostel": "",
    "room": "",
    "phone": "",
    "notes": "",
}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load():
    """The profile, with every known field present (empty if unset)."""
    data = dict(DEFAULTS)
    try:
        with open(PROFILE_FILE, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in DEFAULTS:
                    data[key] = "" if value is None else str(value).strip()
    except (OSError, json.JSONDecodeError):
        pass
    return data


def save(data):
    tmp = PROFILE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({k: data.get(k, "") for k in DEFAULTS}, fh,
                  indent=2, ensure_ascii=False)
    os.replace(tmp, PROFILE_FILE)


def ids(data=None):
    """Every string that means "me" in a document, longest first.

    Longest first so a row containing the campus ID is reported against that
    rather than against a shorter, vaguer match.
    """
    data = data or load()
    found = []
    for key in ("campus_id", "email_id"):
        value = (data.get(key) or "").strip()
        if value and value.lower() not in [f.lower() for f in found]:
            found.append(value)
    return sorted(found, key=len, reverse=True)


def detect_from_store(mails=None):
    """Read name, IDs and campus off your own mail.

    The campus ID is in every mail you have signed, and the display name is on
    the To: line of everything addressed to you - so a fresh install does not
    have to be typed in by hand.
    """
    if mails is None:
        try:
            with open(STORE_FILE, encoding="utf-8") as fh:
                mails = (json.load(fh) or {}).get("mails") or []
        except (OSError, json.JSONDecodeError, AttributeError):
            mails = []

    campus_ids, mail_ids, names = {}, {}, {}
    addr_re = re.compile(r'(?:"?([^"<,]{2,60}?)"?\s*)?<([^>]+)>')

    for mail in mails:
        if not isinstance(mail, dict):
            continue
        text = " ".join(str(mail.get(k) or "") for k in
                        ("subject", "body_text", "snippet"))
        for found in CAMPUS_ID_RE.findall(text.upper()):
            campus_ids[found] = campus_ids.get(found, 0) + 1
        for found in MAIL_ID_RE.findall(text):
            mail_ids[found.lower()] = mail_ids.get(found.lower(), 0) + 1
        for field in ("to", "cc"):
            for display, address in addr_re.findall(str(mail.get(field) or "")):
                display = display.strip(" .")
                if display and mail_ids and address.split("@")[0].lower() in mail_ids:
                    names[display] = names.get(display, 0) + 1

    def best(counts):
        return max(counts.items(), key=lambda kv: kv[1])[0] if counts else ""

    detected = {}
    campus_id = best(campus_ids)
    mail_id = best(mail_ids)
    name = best(names)
    if campus_id:
        detected["campus_id"] = campus_id
        detected["batch"] = campus_id[:4]
    if mail_id:
        detected["email_id"] = mail_id
        detected["email"] = mail_id + "@hyderabad.bits-pilani.ac.in"
    if name:
        detected["name"] = name
    return detected


def apply_detected(overwrite=False):
    """Fill blanks from the store. Returns the fields that changed."""
    data = load()
    detected = detect_from_store()
    changed = {}
    for key, value in detected.items():
        if value and (overwrite or not data.get(key)):
            if data.get(key) != value:
                data[key] = value
                changed[key] = value
    if changed:
        save(data)
    return changed


def facts_block(data=None):
    """The profile as lines for the chat prompt. Empty fields are left out."""
    data = data or load()
    order = ["name", "campus_id", "email_id", "email", "campus", "batch",
             "programme", "hostel", "room", "phone", "notes"]
    labels = {
        "name": "name", "campus_id": "campus ID (used in seating plans and "
        "mark sheets)", "email_id": "mail login", "email": "college address",
        "campus": "campus", "batch": "joined in", "programme": "programme",
        "hostel": "hostel", "room": "hostel room", "phone": "phone",
        "notes": "notes",
    }
    lines = []
    for key in order:
        value = (data.get(key) or "").strip()
        if value:
            lines.append("- {}: {}".format(labels[key], value))
    if not lines:
        return ""
    return "The student you are talking to:\n" + "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Show or edit your profile.")
    parser.add_argument("--detect", action="store_true",
                        help="Fill empty fields from your own mail.")
    parser.add_argument("--overwrite", action="store_true",
                        help="With --detect, replace existing values too.")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="Set a field, e.g. --set room=412")
    args = parser.parse_args()

    if args.detect:
        changed = apply_detected(overwrite=args.overwrite)
        if changed:
            for key, value in changed.items():
                print("detected {}: {}".format(key, value))
        else:
            print("Nothing new to fill in.")

    if args.set:
        data = load()
        for pair in args.set:
            if "=" not in pair:
                print("Ignoring '{}' - expected KEY=VALUE.".format(pair),
                      file=sys.stderr)
                continue
            key, value = pair.split("=", 1)
            key = key.strip().lower()
            if key not in DEFAULTS:
                print("Unknown field '{}'. Known: {}".format(
                    key, ", ".join(sorted(DEFAULTS))), file=sys.stderr)
                continue
            data[key] = value.strip()
            print("set {}: {}".format(key, data[key] or "(cleared)"))
        save(data)

    data = load()
    print()
    print("profile.json")
    width = max(len(k) for k in FIELDS)
    for key in FIELDS:
        value = data.get(key) or ""
        print("  {:<{w}}  {}".format(key, value or "-", w=width))
    missing = [k for k in FIELDS if not data.get(k)]
    if missing:
        print()
        print("Not set (the chat will say it does not know, never guess):")
        print("  " + ", ".join(missing))
        print("Set one with:  python profile.py --set {}=...".format(missing[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
