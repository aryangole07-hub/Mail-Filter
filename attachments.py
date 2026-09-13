#!/usr/bin/env python3
"""
attachments.py - read the documents that come with mail.

Seating plans arrive as spreadsheets, handouts as PDFs, assignment briefs as
Word files. Until this existed the program only ever read the mail's own text,
so "which room is my exam in" could not be answered: the answer was in row
1,204 of an .xlsx nobody opened.

For every attachment this module produces:

  * `text`    - a readable rendering, capped, that the chat can be shown;
  * `matches` - every row or line that contains one of the student's IDs.
                This is computed here, deterministically, not by the model. A
                seating plan has thousands of rows; the model is never asked to
                find the right one, it is handed it.

Supported: .xlsx/.xlsm, .xls, .csv/.tsv, .docx, .pdf, .pptx, and plain text
(.txt, .tst, .md, .log, .json, .ics, .xml, .html). Images, audio and video are
not read. Anything that fails to parse is recorded with the reason instead of
breaking the digest.

The files themselves are saved under attachments/ (gitignored) so the chat
and the viewer can hand them back.
"""

import csv
import html
import io
import os
import re
import zipfile
from datetime import date, datetime
from html.parser import HTMLParser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ATTACH_DIR = os.path.join(SCRIPT_DIR, "attachments")

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS_PER_MAIL = 8
MAX_TEXT_CHARS = 12000
MAX_SHEET_ROWS = 20000
MAX_SAMPLE_ROWS = 25
MAX_MATCH_ROWS = 20
MAX_PDF_PAGES = 60

TEXT_EXTENSIONS = {".txt", ".text", ".tst", ".md", ".log", ".json", ".ics", ".xml"}
HTML_EXTENSIONS = {".html", ".htm"}
SKIPPED_MIME_PREFIXES = ("image/", "video/", "audio/")

MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
}


def extension(filename):
    return os.path.splitext(filename or "")[1].lower()


def kind_for(filename, mime=""):
    """What reader to use: sheet, csv, tsv, docx, pdf, pptx, text, html, skip, unknown."""
    ext = extension(filename)
    mime = (mime or "").lower()
    if ext in (".xlsx", ".xlsm"):
        return "xlsx"
    if ext == ".xls":
        return "xls"
    if ext == ".csv":
        return "csv"
    if ext == ".tsv":
        return "tsv"
    if ext == ".docx":
        return "docx"
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext == ".pptx":
        return "pptx"
    if ext in HTML_EXTENSIONS:
        return "html"
    if ext in TEXT_EXTENSIONS or mime.startswith("text/"):
        return "text"
    if mime.startswith(SKIPPED_MIME_PREFIXES):
        return "skip"
    return "unknown"


def safe_name(filename):
    """A filename that is safe to write, keeping it recognisable."""
    base = os.path.basename((filename or "").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip(" .")
    return (base or "attachment")[:120]


def storage_path(mail_id, index, filename):
    folder = os.path.join(ATTACH_DIR, re.sub(r"[^\w]+", "_", str(mail_id)))
    return os.path.join(folder, "{}-{}".format(int(index), safe_name(filename)))


def mime_for(filename, fallback="application/octet-stream"):
    return MIME_BY_EXTENSION.get(extension(filename), fallback)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_text(data):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _squash(value):
    """Lower-case with all whitespace removed: '2025 B3PS 0420H' == '2025b3ps0420h'."""
    return re.sub(r"\s+", "", str(value or "")).lower()


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) \
            else value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return " ".join(str(value).split())


def _contains_id(text, ids):
    squashed = _squash(text)
    return any(needle and needle in squashed for needle in (_squash(i) for i in ids))


def _cap(text):
    text = text or ""
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS], True


# ---------------------------------------------------------------------------
# Tables (spreadsheets, CSV, tables inside Word files)
# ---------------------------------------------------------------------------

def _header_index(rows):
    """The first row that looks like column names: several non-empty cells."""
    for index, row in enumerate(rows[:10]):
        if sum(1 for cell in row if cell) >= 2:
            return index
    return 0


def render_tables(sheets, ids=()):
    """(text, matches) for a list of (sheet name, rows)."""
    parts, matches = [], []
    for name, rows in sheets:
        rows = [[_cell(c) for c in row] for row in rows]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        head_at = _header_index(rows)
        header = rows[head_at]
        body = rows[head_at + 1:]

        found = []
        if ids:
            for offset, row in enumerate(body):
                if _contains_id(" ".join(row), ids):
                    labelled = {}
                    for col, value in enumerate(row):
                        if not value:
                            continue
                        label = header[col] if col < len(header) and header[col] else \
                            "column {}".format(col + 1)
                        labelled[label] = value
                    found.append({"sheet": name, "row": head_at + offset + 2,
                                  "cells": labelled})
                    if len(found) >= MAX_MATCH_ROWS:
                        break
        matches.extend(found)

        lines = ["Sheet \"{}\" - {} rows".format(name, len(body))]
        lines.append("columns: " + " | ".join(h or "-" for h in header))
        if found:
            lines.append("rows that contain the student's ID:")
            for match in found:
                lines.append("  row {}: ".format(match["row"]) + "; ".join(
                    "{} = {}".format(k, v) for k, v in match["cells"].items()))
        lines.append("first rows:")
        for row in body[:MAX_SAMPLE_ROWS]:
            lines.append("  " + " | ".join(row))
        if len(body) > MAX_SAMPLE_ROWS:
            lines.append("  ... {} more rows".format(len(body) - MAX_SAMPLE_ROWS))
        parts.append("\n".join(lines))
    return "\n\n".join(parts), matches


class _TableParser(HTMLParser):
    """Rows of every <table> in an HTML document."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._rows = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._rows is not None:
            if len(self._rows) < MAX_SHEET_ROWS:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables.append(self._rows)
            self._rows = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _is_markup(data):
    return (data or b"").lstrip()[:1] == b"<"


def read_html_tables(data):
    """An HTML page's tables as sheets.

    College ERP portals commonly export "Excel" files that are really an HTML
    page with an .xls name - the first one found in the real inbox was exactly
    that. xlrd rejects them ("Expected BOF record"), so they are read as the
    tables they are, and ID matching works on them like any spreadsheet.
    """
    parser = _TableParser()
    parser.feed(_decode_text(data))
    parser.close()
    return [("table {}".format(n), rows)
            for n, rows in enumerate(parser.tables, start=1) if rows]


def read_xlsx(data):
    if _is_markup(data):
        return read_html_tables(data)
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = []
    try:
        for sheet in book.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) >= MAX_SHEET_ROWS:
                    break
            sheets.append((sheet.title, rows))
    finally:
        book.close()
    return sheets


def read_xls(data):
    if _is_markup(data):
        return read_html_tables(data)
    import xlrd
    book = xlrd.open_workbook(file_contents=data)
    sheets = []
    for sheet in book.sheets():
        rows = []
        for r in range(min(sheet.nrows, MAX_SHEET_ROWS)):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                        continue
                    except Exception:  # noqa: BLE001 - fall back to the raw value
                        pass
                row.append(cell.value)
            rows.append(row)
        sheets.append((sheet.name, rows))
    return sheets


def read_delimited(data, delimiter):
    text = _decode_text(data)
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        rows.append(row)
        if len(rows) >= MAX_SHEET_ROWS:
            break
    return [("data", rows)]


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def _line_matches(text, ids):
    found = []
    if not ids:
        return found
    for number, line in enumerate((text or "").splitlines(), start=1):
        if line.strip() and _contains_id(line, ids):
            found.append({"line": number, "text": " ".join(line.split())[:300]})
            if len(found) >= MAX_MATCH_ROWS:
                break
    return found


def read_pdf(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise ValueError("the PDF is password-protected")
    pages = []
    for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append("[page {}]\n{}".format(number, text.strip()))
    return "\n\n".join(pages)


def read_docx(data):
    """(text, tables) - paragraphs in order, and every table as rows."""
    import docx
    document = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    tables = []
    for number, table in enumerate(document.tables, start=1):
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        tables.append(("table {}".format(number), rows))
    return "\n".join(paragraphs), tables


def read_pptx(data):
    slides = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = sorted(
            (n for n in archive.namelist()
             if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        for number, name in enumerate(names, start=1):
            xml = archive.read(name).decode("utf-8", "replace")
            texts = [html.unescape(t) for t in re.findall(r"<a:t>([^<]*)</a:t>", xml)]
            if texts:
                slides.append("[slide {}] {}".format(number, " ".join(texts)))
    return "\n".join(slides)


def html_to_text(raw):
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", raw).strip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def extract(filename, mime, data, ids=()):
    """Everything readable about one attachment. Never raises."""
    kind = kind_for(filename, mime)
    result = {
        "filename": filename or "attachment",
        "mime": mime or mime_for(filename),
        "size": len(data or b""),
        "kind": kind,
        "text": "",
        "matches": [],
        "truncated": False,
        "error": "",
    }
    if not data:
        result["error"] = "empty file"
        return result
    if kind in ("skip", "unknown"):
        result["error"] = ("images, audio and video are not read" if kind == "skip"
                           else "this file type is not read")
        return result

    try:
        if kind in ("xlsx", "xls", "csv", "tsv"):
            reader = {"xlsx": read_xlsx, "xls": read_xls,
                      "csv": lambda d: read_delimited(d, ","),
                      "tsv": lambda d: read_delimited(d, "\t")}[kind]
            text, matches = render_tables(reader(data), ids)
        elif kind == "docx":
            body, tables = read_docx(data)
            table_text, matches = render_tables(tables, ids)
            matches = _line_matches(body, ids) + matches
            text = body + ("\n\n" + table_text if table_text else "")
        elif kind == "pdf":
            text = read_pdf(data)
            matches = _line_matches(text, ids)
            if not text.strip():
                result["error"] = "no text found (probably a scanned image)"
        elif kind == "pptx":
            text = read_pptx(data)
            matches = _line_matches(text, ids)
        elif kind == "html":
            text = html_to_text(_decode_text(data))
            matches = _line_matches(text, ids)
        else:
            text = _decode_text(data)
            matches = _line_matches(text, ids)
    except Exception as exc:  # noqa: BLE001 - one bad file must not sink the digest
        result["error"] = "could not read it ({}: {})".format(
            exc.__class__.__name__, " ".join(str(exc).split())[:120])
        return result

    result["text"], result["truncated"] = _cap(text)
    result["matches"] = matches
    return result


def describe_match(match):
    if "cells" in match:
        return "; ".join("{} = {}".format(k, v) for k, v in match["cells"].items())
    return match.get("text", "")


def facts_block(mails, limit=8):
    """Rows mentioning the student's ID, across every stored attachment.

    Given to the chat on every turn, retrieval or not: "which room is my exam
    in" should never depend on whether the seating mail happened to score well.
    Newest mail first.
    """
    found = []
    for mail in mails or []:
        if not isinstance(mail, dict):
            continue
        for attachment in mail.get("attachments") or []:
            for match in attachment.get("matches") or []:
                found.append((mail.get("received_at") or "", mail, attachment, match))
    if not found:
        return ""
    found.sort(key=lambda item: item[0], reverse=True)
    lines = []
    for received, mail, attachment, match in found[:limit]:
        where = ("row {}".format(match["row"]) if "row" in match
                 else "line {}".format(match.get("line", "?")))
        sheet = " (sheet \"{}\")".format(match["sheet"]) if match.get("sheet") else ""
        lines.append("- {}{}, {} - from the mail \"{}\" received {}: {}".format(
            attachment.get("filename"), sheet, where, mail.get("subject", ""),
            (received or "")[:10], describe_match(match)))
    return ("Rows that contain the student's own ID in documents attached to "
            "their mail (found by exact ID match, so reliable; newest first). "
            "Use these for seat, room, venue, group or marks questions and say "
            "which file it came from:\n" + "\n".join(lines))
