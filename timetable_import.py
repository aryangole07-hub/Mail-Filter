#!/usr/bin/env python3
"""
timetable_import.py - turn a screenshot of the ERP weekly timetable into
timetable.json, so a friend's install knows their courses, rooms, times and
instructors without anyone typing them in.

The screenshot is the ERP "Schedule" grid with every Display Option ticked:
one coloured box per class, each reading

    ECON F212 - L1
    FUNDA OF FIN AND ACCOUNT
    Lecture
    9:00AM - 9:50AM
    F BLOCK F207
    Instructors:
    UTKARSH KUMAR ., SHOBHANA SIKHAWAL .

How it is read - the parts that can be done exactly are done exactly:

  1. boxes are found by colour, and each is assigned its weekday from the
     table's own vertical grid lines (no model involved);
  2. each box, cropped and enlarged, is transcribed by the local model -
     on the CPU only, through mail_filter.OllamaClient;
  3. the transcription is parsed with fixed patterns (course code, section,
     time range, room, instructors), and every field that could not be found
     is reported, box by box, so setup can warn and let the student continue
     regardless or pick a better picture.

The result is written to timetable.json, which courses.py loads in place of
its built-in timetable.
"""

import base64
import io
import json
import os
import re
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TIMETABLE_FILE = os.path.join(SCRIPT_DIR, "timetable.json")

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
FIELDS = ["code", "section", "type", "start", "end", "room", "instructors"]
FIELD_LABELS = {"code": "course code", "section": "section (L1/T1/P1)",
                "type": "lecture/tutorial", "start": "start time", "end": "end time",
                "room": "room", "instructors": "instructors", "name": "course name"}

CODE_RE = re.compile(r"\b([A-Z]{2,5})\s*F\s*(\d{3})\b(?:\s*[-–—]\s*([LTP]\s*\d{1,2}))?")
SECTION_RE = re.compile(r"(?:^|[\s-])([LTP]\s*\d{1,2})\b")
TIME_RE = re.compile(r"(\d{1,2})\s*[:.]\s*(\d{2})\s*([AP])\.?\s*M\b", re.I)
TYPE_RE = re.compile(r"\b(lecture|tutorial|practical|laboratory|lab)\b", re.I)
ROOM_RE = re.compile(r"\b([A-Z])\s*[-]?\s*BLOCK\s*([A-Z]?\s*\d{2,4}[A-Z]?)\b", re.I)
INSTRUCTORS_RE = re.compile(r"instructors?\s*[:;]\s*(.*)$", re.I | re.S)

TRANSCRIBE_PROMPT = (
    "This is one box from a university class timetable. Transcribe every line of "
    "text in it exactly as written, top to bottom, keeping the line breaks. Do not "
    "correct, reorder or explain anything. Output only the text.")


# ---------------------------------------------------------------------------
# Reading one box's text
# ---------------------------------------------------------------------------

def _hhmm(hour, minute, half):
    hour, minute = int(hour), int(minute)
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return ""
    if half.upper() == "P" and hour != 12:
        hour += 12
    if half.upper() == "A" and hour == 12:
        hour = 0
    return "{:02d}:{:02d}".format(hour, minute)


def _title(words):
    """"FUNDA OF FIN AND ACCOUNT" -> "Funda of Fin and Account"."""
    small = {"of", "and", "the", "in", "for", "to", "&"}
    out = []
    for n, word in enumerate(words.split()):
        low = word.lower()
        out.append(low if n and low in small else low[:1].upper() + low[1:])
    return " ".join(out)


def parse_cell(text):
    """One box's text -> (slot dict, list of missing field names)."""
    lines = [" ".join(line.split()) for line in (text or "").splitlines() if line.strip()]
    joined = " ".join(lines)
    upper = joined.upper()

    slot = {"code": "", "section": "", "name": "", "type": "", "start": "", "end": "",
            "room": "", "instructors": []}

    code = CODE_RE.search(upper)
    if code:
        slot["code"] = "{} F{}".format(code.group(1), code.group(2))
        if code.group(3):
            slot["section"] = code.group(3).replace(" ", "")
    if not slot["section"]:
        section = SECTION_RE.search(upper[code.end():] if code else upper)
        if section:
            slot["section"] = section.group(1).replace(" ", "")

    kind = TYPE_RE.search(joined)
    if kind:
        word = kind.group(1).lower()
        slot["type"] = {"lab": "Practical", "laboratory": "Practical"}.get(word, word.title())
    elif slot["section"][:1] in ("L", "T", "P"):
        slot["type"] = {"L": "Lecture", "T": "Tutorial", "P": "Practical"}[slot["section"][0]]

    times = TIME_RE.findall(joined)
    if times:
        slot["start"] = _hhmm(*times[0])
    if len(times) > 1:
        slot["end"] = _hhmm(*times[1])

    room = ROOM_RE.search(joined)
    if room:
        slot["room"] = "{} Block {}".format(room.group(1).upper(),
                                            room.group(2).replace(" ", "").upper())

    who = INSTRUCTORS_RE.search(joined)
    if who:
        names = []
        for part in re.split(r",|;|\band\b", who.group(1)):
            name = re.sub(r"[.\s]+$", "", " ".join(part.replace(".", " ").split()))
            if re.search(r"[A-Za-z]{2}", name):
                names.append(_title(name))
        slot["instructors"] = names

    # The course name sits between the code and the class type (or the time).
    if code:
        start = code.end()
        stops = [m.start() for m in (TYPE_RE.search(upper, start), TIME_RE.search(upper, start))
                 if m]
        end = min(stops) if stops else len(upper)
        name = re.sub(r"^[\s\-–—]*(?:[LTP]\s*\d{1,2})?[\s\-–—]*", "", upper[start:end]).strip(" -")
        slot["name"] = _title(name) if re.search(r"[A-Z]{2}", name) else ""

    missing = [f for f in FIELDS if not slot[f]]
    if slot["start"] and slot["end"] and slot["end"] <= slot["start"]:
        missing.append("end")  # an end before the start is a misread time
        slot["end"] = ""
    return slot, sorted(set(missing), key=FIELDS.index)


# ---------------------------------------------------------------------------
# Finding the boxes in the picture
# ---------------------------------------------------------------------------

def _tinted(pixel):
    return max(pixel) - min(pixel) > 28 and min(pixel) > 80


def _fill(pixel):
    """A box's own colour, not the very light line the ERP draws between boxes.

    Measured on a real screenshot: box fill is about (183, 209, 146); the
    one-pixel separator between side-by-side boxes is about (223, 239, 203).
    Counting the separator as box colour merged a whole row of five classes
    into one box, so anything that light is excluded.
    """
    return _tinted(pixel) and min(pixel) < 195


def _gridline(pixel):
    """The faint grey of a table border, not white paper and not a coloured box."""
    return max(pixel) - min(pixel) < 14 and 150 <= min(pixel) <= 238


def column_edges(image):
    """x positions of the table's vertical grid lines, left to right."""
    width, height = image.size
    px = image.load()
    counts = []
    for x in range(width):
        counts.append(sum(1 for y in range(0, height, 2) if _gridline(px[x, y])))
    threshold = 0.35 * (height / 2)
    edges, run = [], None
    for x in range(width + 1):
        on = x < width and counts[x] >= threshold
        if on and run is None:
            run = x
        if not on and run is not None:
            edges.append((run + x - 1) // 2)
            run = None
    merged = []
    for x in edges:
        if merged and x - merged[-1] < 12:
            continue
        merged.append(x)
    return merged


def find_boxes(image):
    """[(x0, y0, x1, y1), ...] of every coloured class box.

    Columns first, by how much box colour each x holds (the light separators
    between side-by-side boxes hold none); then, inside each column, rows by
    the share of the column's width that is box colour - text inside a box
    covers only part of a row, while the separator between stacked boxes and
    the white space between classes cover all of it.
    """
    width, height = image.size
    px = image.load()
    col = [sum(1 for y in range(0, height, 2) if _fill(px[x, y])) for x in range(width)]
    bands, start = [], None
    for x in range(width + 1):
        on = x < width and col[x] > 30
        if on and start is None:
            start = x
        if not on and start is not None:
            if x - start > 40:
                bands.append((start, x - 1))
            start = None
    boxes = []
    for x0, x1 in bands:
        samples = list(range(x0, x1 + 1, 2))
        run = None
        for y in range(height + 1):
            on = y < height and sum(1 for x in samples if _fill(px[x, y])) > 0.3 * len(samples)
            if on and run is None:
                run = y
            if not on and run is not None:
                if y - run > 30:
                    boxes.append((x0, run, x1, y - 1))
                run = None
    return boxes


def _boundaries(boxes, edges, width):
    """Every column boundary: the grey grid lines, plus the gaps between boxes.

    Where classes sit side by side the grey line is covered, and the only trace
    of the column boundary is the separator between the two boxes - so those
    gaps count as boundaries too.
    """
    points = set(edges or []) | {0, width}
    spans = sorted({(b[0], b[2]) for b in boxes})
    for (_, right), (left, _) in zip(spans, spans[1:]):
        if 0 < left - right < 20:
            points.add((right + left) // 2)
    merged = []
    for x in sorted(points):
        if merged and x - merged[-1] < 12:
            continue
        merged.append(x)
    return merged


def assign_days(boxes, edges, width):
    """(day index or None, box) for each box, using the table's column lines.

    The table has a Time column and seven day columns - eight columns. When all
    eight can be measured, each box goes to the column holding its centre. When
    they cannot, the boxes' own columns are taken as Monday onwards, and that
    is reported.
    """
    cells = []
    bounds = _boundaries(boxes, edges, width)
    for left, right in zip(bounds, bounds[1:]):
        if right - left > 40:
            cells.append((left, right))
    reliable = len(cells) == 8
    day_cells = cells[1:] if reliable else []

    result = []
    if reliable:
        for box in boxes:
            centre = (box[0] + box[2]) / 2
            day = next((n for n, (l, r) in enumerate(day_cells) if l <= centre < r), None)
            result.append((day, box))
        return result, True

    columns = sorted({box[0] for box in boxes})
    for box in boxes:
        index = columns.index(box[0])
        result.append((index if index < 7 else None, box))
    return result, False


# ---------------------------------------------------------------------------
# The whole import
# ---------------------------------------------------------------------------

def transcribe(client, model, crop):
    """One box's text, read by the local model on the CPU."""
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    reply = client.messages.create(
        model=model, max_tokens=300, num_ctx=4096,
        messages=[{"role": "user", "content": TRANSCRIBE_PROMPT,
                   "images": [base64.b64encode(buf.getvalue()).decode("ascii")]}])
    for block in getattr(reply, "content", None) or []:
        if getattr(block, "text", ""):
            return block.text
    return ""


def import_screenshot(path, client=None, model="gemma3:4b", read=None, progress=None):
    """Read a timetable screenshot. Returns {"slots", "warnings", "boxes"}.

    `read(crop) -> text` can replace the model (tests, or a different reader).
    `progress(done, total)` is called after each box.
    """
    from PIL import Image

    image = Image.open(path).convert("RGB")
    width, _ = image.size
    boxes = find_boxes(image)
    warnings = []
    if not boxes:
        return {"slots": [], "boxes": 0, "warnings": [
            "No class boxes were found in this picture. Take the screenshot of the "
            "whole weekly schedule grid, with the coloured boxes visible."]}

    placed, reliable = assign_days(boxes, column_edges(image), width)
    if not reliable:
        warnings.append("The day columns could not be measured from the grid lines, "
                        "so boxes were read left to right as Monday onwards. Check the "
                        "days after setup.")

    reader = read or (lambda crop: transcribe(client, model, crop))
    slots = []
    for number, (day, box) in enumerate(placed, start=1):
        crop = image.crop(box)
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        slot, missing = parse_cell(reader(crop))
        slot["day"] = day
        where = "{} box {}".format(DAY_NAMES[day] if day is not None else "A", number)
        if slot["start"]:
            where = "{} {}".format(DAY_NAMES[day] if day is not None else "Unknown day",
                                   slot["start"])
        if day is None:
            missing = ["day"] + missing
        if missing:
            warnings.append("{} ({}): could not read the {}.".format(
                where, slot["code"] or "unknown course",
                ", ".join(FIELD_LABELS.get(f, f) for f in missing)))
        slots.append(slot)
        if progress:
            progress(number, len(placed))

    seen = {}
    for slot in slots:
        key = (slot["day"], slot["start"])
        if slot["day"] is not None and slot["start"] and key in seen:
            warnings.append("{} {}: two classes were read at the same time ({} and {}).".format(
                DAY_NAMES[slot["day"]], slot["start"], seen[key], slot["code"] or "unknown"))
        seen.setdefault(key, slot["code"] or "unknown")
    return {"slots": slots, "boxes": len(boxes), "warnings": warnings}


def acronym(name):
    words = [w for w in re.findall(r"[A-Za-z]+", name or "")
             if w.lower() not in {"of", "and", "the", "in", "for", "to", "a", "an"}]
    return "".join(w[0].upper() for w in words) if len(words) > 1 else ""


def build_timetable(slots, known=None):
    """The timetable.json structure courses.py loads.

    `known` (courses.BY_CODE) lends short forms and aliases for a course code
    it already knows, so a friend in the same batch gets "FoFA" rather than "FFA".
    """
    known = known or {}
    usable = [s for s in slots if s.get("code") and s.get("day") is not None and s.get("start")]
    registry = {}
    for slot in usable:
        entry = registry.setdefault(slot["code"], {
            "code": slot["code"], "name": "", "short": [], "aka": [], "profs": []})
        if slot.get("name") and not entry["name"]:
            entry["name"] = slot["name"]
        for person in slot.get("instructors") or []:
            if person not in entry["profs"]:
                entry["profs"].append(person)
    for code, entry in registry.items():
        base = known.get(code) or {}
        if base.get("name"):
            if entry["name"] and entry["name"].lower() != base["name"].lower():
                entry["aka"].append(entry["name"])
            entry["name"] = base["name"]
        entry["short"] = list(base.get("short") or []) or (
            [acronym(entry["name"])] if acronym(entry["name"]) else [])
        entry["aka"] = list(dict.fromkeys(list(base.get("aka") or []) + entry["aka"]))
        if not entry["name"]:
            entry["name"] = code

    weekly, slot_profs = [], {}
    for slot in sorted(usable, key=lambda s: (s["day"], s["start"])):
        ctype = slot.get("type") or "Lecture"
        weekly.append({"day": slot["day"], "start": slot["start"],
                       "end": slot.get("end") or "", "code": slot["code"], "type": ctype,
                       "section": slot.get("section") or "", "room": slot.get("room") or ""})
        people = slot_profs.setdefault((slot["code"], ctype), [])
        for person in slot.get("instructors") or []:
            if person not in people:
                people.append(person)

    return {
        "source": "screenshot",
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "courses": sorted(registry.values(), key=lambda c: c["code"]),
        "weekly": weekly,
        "slot_profs": [{"code": code, "type": ctype, "profs": people}
                       for (code, ctype), people in sorted(slot_profs.items())],
    }


def save(timetable, path=None):
    path = path or TIMETABLE_FILE
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(timetable, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Import a timetable screenshot.")
    parser.add_argument("image", help="Screenshot of the ERP weekly schedule")
    parser.add_argument("--save", action="store_true", help="Write timetable.json")
    args = parser.parse_args()

    import courses
    import mail_filter

    result = import_screenshot(
        args.image, client=mail_filter.OllamaClient(), model=mail_filter.CLASSIFY_MODEL,
        progress=lambda done, total: print("  read box {}/{}".format(done, total),
                                           file=sys.stderr, flush=True))
    timetable = build_timetable(result["slots"], known=courses.BY_CODE)
    print("boxes found: {} | classes read: {} | courses: {}".format(
        result["boxes"], len(timetable["weekly"]), len(timetable["courses"])))
    for slot in timetable["weekly"]:
        print("  {} {}-{} {} {} {} {}".format(DAY_NAMES[slot["day"]][:3], slot["start"],
                                              slot["end"], slot["code"], slot["section"],
                                              slot["type"], slot["room"]))
    for warning in result["warnings"]:
        print("WARNING: " + warning)
    if args.save:
        print("saved to " + save(timetable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
