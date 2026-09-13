#!/usr/bin/env python3
"""
user_notes.py - things the student knows that no email says.

"The prof said in class the quiz covers chapters 3-5", "tutorial moved to
Friday for this week only", "my lab partner is ...". None of that is in the
inbox, so the chat could never know it. The Notes tab writes it here, and the
chat is given every note on every turn, labelled as the student's own words.

Stored in user_notes.json, which is gitignored: it is personal.
"""

import json
import os
import uuid
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NOTES_FILE = os.path.join(SCRIPT_DIR, "user_notes.json")

MAX_NOTE_CHARS = 2000
MAX_NOTES = 300
# How much note text the chat is shown. Newest notes win if there is more.
MAX_PROMPT_CHARS = 6000


def load():
    try:
        with open(NOTES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    notes = data.get("notes") if isinstance(data, dict) else data
    if not isinstance(notes, list):
        return []
    return [n for n in notes
            if isinstance(n, dict) and isinstance(n.get("text"), str) and n.get("id")]


def _save(notes):
    tmp = NOTES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"notes": notes[-MAX_NOTES:]}, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, NOTES_FILE)


def add(text):
    """Store one note. Returns the note, or None if it was empty."""
    text = (text or "").strip()
    if not text:
        return None
    note = {
        "id": uuid.uuid4().hex[:12],
        "text": text[:MAX_NOTE_CHARS],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    notes = load()
    notes.append(note)
    _save(notes)
    return note


def delete(note_id):
    notes = load()
    kept = [n for n in notes if n.get("id") != note_id]
    if len(kept) == len(notes):
        return False
    _save(kept)
    return True


def facts_block(notes=None):
    """Notes for the chat prompt, newest first, capped."""
    notes = load() if notes is None else notes
    if not notes:
        return ""
    lines, used = [], 0
    for note in sorted(notes, key=lambda n: n.get("created_at") or "", reverse=True):
        line = "- (added {}) {}".format((note.get("created_at") or "")[:10],
                                       " ".join(note["text"].split()))
        if used + len(line) > MAX_PROMPT_CHARS:
            break
        lines.append(line)
        used += len(line)
    return ("Things the student has told you themselves - from class, from "
            "friends, from notice boards - that are not in any email. Treat them "
            "as reliable. When an answer rests on one, say it is from their notes; "
            "when a note and an email disagree, point that out:\n" + "\n".join(lines))
