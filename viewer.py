#!/usr/bin/env python3
"""
viewer.py - the local web viewer for the mail digest.

Serves a small site on 127.0.0.1 showing each important mail's heading, an
AI summary, and a button that opens the original message exactly as it was
sent. Nothing is exposed to the network and nothing is uploaded anywhere:
the server binds to the loopback interface only, and every byte it serves
comes from digest_store.json on this machine.

Run it with:  .\\.venv\\Scripts\\python.exe viewer.py
"""

import http.server
import json
import os
import re
import socket
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(SCRIPT_DIR, "digest_store.json")
FEEDBACK_FILE = os.path.join(SCRIPT_DIR, "feedback.json")

HOST = "127.0.0.1"  # loopback only - never 0.0.0.0, see module docstring
DEFAULT_PORT = 8765

CATEGORY_ORDER = ["Classes", "Fests", "Other"]
CATEGORY_EMOJI = {"Classes": "\U0001F4DA", "Fests": "\U0001F389", "Other": "\U0001F4CC"}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_store():
    try:
        with open(STORE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("mails"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"generated_at": None, "mails": []}


def load_feedback():
    try:
        with open(FEEDBACK_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("reports"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"reports": []}


def save_feedback(data):
    """Atomic write, so an interrupted save cannot truncate the file."""
    tmp = FEEDBACK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, FEEDBACK_FILE)


def add_report(mail_id, undo=False):
    """Record (or withdraw) a "not important" report for one mail."""
    store = load_store()
    mail = next((m for m in store["mails"] if m.get("id") == mail_id), None)
    if mail is None:
        return False, "unknown mail id"

    data = load_feedback()
    reports = [r for r in data.get("reports", []) if r.get("id") != mail_id]

    if not undo:
        reports.append({
            "id": mail_id,
            "subject": mail.get("subject", ""),
            "sender_address": mail.get("from_address", ""),
            "sender": mail.get("from", ""),
            "was_category": mail.get("category", ""),
            "reported_at": datetime.now(timezone.utc).isoformat(),
        })

    data["reports"] = reports
    save_feedback(data)
    return True, len(reports)


def reported_ids():
    return {r.get("id") for r in load_feedback().get("reports", [])}


def important_ids():
    return {r.get("id") for r in load_feedback().get("important", [])}


def important_senders():
    return {(r.get("sender_address") or "").lower()
            for r in load_feedback().get("important", []) if r.get("sender_address")}


def mark_important(mail_id, undo=False):
    """Record that a mail the program hid actually mattered.

    Marking also clears any report on the same sender: the student has just
    said, in the strongest terms available to them, that this sender's mail
    must be shown, and leaving a stale report in place would let the mistake
    repeat.
    """
    store = load_store()
    mail = next((m for m in store["mails"] if m.get("id") == mail_id), None)
    if mail is None:
        return False, "unknown mail id"

    data = load_feedback()
    marked = [r for r in data.get("important", []) if r.get("id") != mail_id]
    address = (mail.get("from_address") or "").lower()

    if not undo:
        marked.append({
            "id": mail_id,
            "subject": mail.get("subject", ""),
            "sender_address": address,
            "sender": mail.get("from", ""),
            "was_category": mail.get("category", ""),
            "marked_at": datetime.now(timezone.utc).isoformat(),
        })
        if address:
            data["reports"] = [
                r for r in data.get("reports", [])
                if (r.get("sender_address") or "").lower() != address
            ]

    data["important"] = marked
    save_feedback(data)
    return True, len(marked)


def mails_for_ui():
    """The store, minus the bodies, plus whether each has been reported.

    Bodies are deliberately left out of this payload: they are the bulk of the
    file and are only needed when a message is actually opened, which the
    /original endpoint handles one at a time.
    """
    store = load_store()
    reported = reported_ids()
    marked = important_ids()
    marked_senders = important_senders()
    out = []
    for m in store["mails"]:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out.append({
            "id": m["id"],
            "subject": m.get("subject") or "(no subject)",
            "from": m.get("from") or "",
            "from_address": m.get("from_address") or "",
            "date": m.get("date") or "",
            "received_at": m.get("received_at") or "",
            "category": m.get("category") or "Other",
            "summary": m.get("summary") or "",
            "snippet": m.get("snippet") or "",
            "institution": bool(m.get("institution")),
            "courses": m.get("courses") or [],
            "events": m.get("events") or [],
            "rescued": bool(m.get("rescued")),
            "rescue_reason": m.get("rescue_reason") or "",
            "marked_important": (
                m["id"] in marked
                or (m.get("from_address") or "").lower() in marked_senders
            ),
            # Marks mail, or a mail the student has personally corrected.
            "absolute": bool(m.get("absolute")) or m["id"] in marked,
            # A reported mail is hidden from the main list - unless nothing is
            # allowed to hide it.
            "reported": (
                m["id"] in reported
                and not m.get("absolute")
                and m["id"] not in marked
            ),
            "has_body": bool(m.get("body_html") or m.get("body_text")),
        })
    return {"generated_at": store.get("generated_at"), "mails": out}


_CLIENT = None


def model_client():
    """The local Ollama client, imported on first use.

    Deferred so the viewer opens and lists mail even when Ollama is not
    running - only the Ask tab needs it, and it says so itself when it fails.
    """
    global _CLIENT
    if _CLIENT is None:
        import mail_filter
        _CLIENT = (mail_filter.OllamaClient(), mail_filter.CLASSIFY_MODEL)
    return _CLIENT


def calendar_entries(start_date, end_date):
    """Timetable classes plus every dated thing found in mail, in one list."""
    import courses

    entries = list(courses.classes_between(start_date, end_date))

    reported = reported_ids()
    marked = important_ids()
    for mail in load_store()["mails"]:
        if not isinstance(mail, dict):
            continue
        hidden = (
            mail.get("category") == "Ignore" or mail.get("id") in reported
        ) and not (mail.get("absolute") or mail.get("id") in marked)
        if hidden:
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
            entry = dict(event)
            entry["source"] = "mail"
            entry["mail_id"] = mail.get("id")
            entry["mail_subject"] = mail.get("subject", "")
            entry["courses"] = mail.get("courses") or []
            entries.append(entry)

    entries.sort(key=lambda e: (e.get("date", ""), e.get("start_time") or "99:99"))
    return entries


# ---------------------------------------------------------------------------
# Rendering the original message
# ---------------------------------------------------------------------------

def original_document(mail, allow_images=False):
    """The message exactly as it arrived, wrapped for safe display.

    The body itself is passed through unchanged - that is the entire point of
    the Original button. Safety comes from how it is *displayed*, not from
    editing it: the page is served with a Content-Security-Policy that permits
    no scripts, no frames and no network access of any kind, and the viewer
    loads it inside a sandboxed iframe. Remote images stay blocked until the
    reader asks for them, because a remote image in an email is usually a
    tracking pixel that reports back when the mail was read.
    """
    body_html = mail.get("body_html") or ""
    if not body_html:
        text = mail.get("body_text") or mail.get("snippet") or "(no content)"
        body_html = "<pre class='plain'>" + escape_html(text) + "</pre>"

    img_src = "img-src data: https: http: cid:;" if allow_images else "img-src data:;"
    csp = (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        + img_src +
        " font-src data:; form-action 'none'; base-uri 'none'; frame-ancestors 'self';"
    )

    header_rows = []
    for label, value in (
        ("From", mail.get("from")),
        ("To", mail.get("to")),
        ("Cc", mail.get("cc")),
        ("Date", mail.get("date")),
        ("Subject", mail.get("subject")),
    ):
        if value:
            header_rows.append(
                "<tr><th>" + escape_html(label) + "</th><td>"
                + escape_html(value) + "</td></tr>"
            )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta http-equiv='Content-Security-Policy' content=\"" + csp + "\">"
        "<style>"
        "body{margin:0;padding:20px;font:14px/1.6 system-ui,-apple-system,"
        "Segoe UI,sans-serif;color:#111;background:#fff;}"
        "table.hdr{border-collapse:collapse;margin-bottom:18px;width:100%;}"
        "table.hdr th{text-align:left;vertical-align:top;padding:3px 12px 3px 0;"
        "color:#666;font-weight:600;white-space:nowrap;width:1%;font-size:12px;"
        "text-transform:uppercase;letter-spacing:.04em;}"
        "table.hdr td{padding:3px 0;word-break:break-word;}"
        "hr{border:0;border-top:1px solid #e5e5e5;margin:0 0 18px;}"
        "pre.plain{white-space:pre-wrap;word-wrap:break-word;font:inherit;margin:0;}"
        "img{max-width:100%;height:auto;}"
        "</style></head><body>"
        "<table class='hdr'>" + "".join(header_rows) + "</table><hr>"
        + body_html +
        "</body></html>"
    )


def escape_html(text):
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

UI_FILE = os.path.join(SCRIPT_DIR, "ui.html")

# Fallback shown only if ui.html is missing, so a broken install says why
# instead of serving a blank page.
FALLBACK_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>Mail Filter</title>"
    "<body style=\"font:15px system-ui;padding:40px;max-width:40em;margin:auto\">"
    "<h1>ui.html is missing</h1><p>The viewer serves its page from "
    "<code>ui.html</code>, which should sit next to <code>viewer.py</code>. "
    "Re-download it from the repository.</p>"
)


def page():
    """The UI, read from disk each request so an edit shows on refresh."""
    try:
        with open(UI_FILE, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return FALLBACK_PAGE




# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MailFilterViewer/1.0"

    def log_message(self, fmt, *args):
        pass  # the console is for the digest, not an access log

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self._send(200, page())

        if path == "/api/mails":
            return self._json(200, mails_for_ui())

        if path == "/api/courses":
            import courses
            return self._json(200, {"courses": courses.registry_for_ui()})

        if path == "/api/calendar":
            params = urllib.parse.parse_qs(parsed.query)
            today = datetime.now().astimezone().date()  # calendar day, local
            try:
                start = datetime.strptime(
                    params.get("from", [""])[0], "%Y-%m-%d").date()
            except ValueError:
                start = today - timedelta(days=31)
            try:
                end = datetime.strptime(
                    params.get("to", [""])[0], "%Y-%m-%d").date()
            except ValueError:
                end = today + timedelta(days=62)
            if end < start:
                start, end = end, start
            if (end - start).days > 400:
                end = start + timedelta(days=400)
            return self._json(200, {"entries": calendar_entries(start, end)})

        if path.startswith("/original/"):
            mail_id = urllib.parse.unquote(path[len("/original/"):])
            store = load_store()
            mail = next((m for m in store["mails"] if m.get("id") == mail_id), None)
            if mail is None:
                return self._send(404, "<p>That message is no longer in the store.</p>")
            params = urllib.parse.parse_qs(parsed.query)
            allow = params.get("images", ["0"])[0] == "1"
            doc = original_document(mail, allow_images=allow)
            csp = (
                "default-src 'none'; style-src 'unsafe-inline'; font-src data:; "
                + ("img-src data: https: http: cid:;" if allow else "img-src data:;")
                + " script-src 'none'; form-action 'none'; base-uri 'none';"
            )
            return self._send(200, doc, extra={"Content-Security-Policy": csp})

        return self._send(404, "<p>Not found.</p>")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/api/report", "/api/important", "/api/ask"):
            return self._json(404, {"error": "not found"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 64_000:
            return self._json(400, {"error": "bad request body"})

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "expected JSON"})

        # Ask carries a question, not a mail id, so it is routed before the
        # id check the other two endpoints need.
        if path == "/api/ask":
            status, body = _ask(payload)
            return self._json(status, body)

        mail_id = payload.get("id")
        if not mail_id:
            return self._json(400, {"error": "expected JSON with an id"})

        if path == "/api/important":
            ok, detail = mark_important(mail_id, undo=bool(payload.get("undo")))
        else:
            ok, detail = add_report(mail_id, undo=bool(payload.get("undo")))
        if not ok:
            return self._json(404, {"error": detail})
        return self._json(200, {"ok": True, "count": detail})


def _ask(payload):
    """Answer a question from the store. Returns (status, body)."""
    import qa

    question = str(payload.get("question") or "").strip()
    if not question:
        return 400, {"error": "expected a question"}
    if len(question) > 500:
        question = question[:500]

    try:
        client, model = model_client()
    except Exception as exc:  # noqa: BLE001
        return 200, {"ok": False, "sources": [],
                     "answer": "Could not reach the local model ({}).".format(exc)}

    result = qa.ask(client, model, load_store()["mails"], question)
    return 200, result


def free_port(preferred):
    """The preferred port, or the next free one after it."""
    for port in range(preferred, preferred + 20):
        with socket.socket() as sock:
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                continue
    return 0  # let the OS choose


def main():
    if not os.path.exists(STORE_FILE):
        print("No digest yet - run mail_filter.py first, then start this viewer.")
        return 1

    port = free_port(DEFAULT_PORT)
    httpd = http.server.ThreadingHTTPServer((HOST, port), Handler)
    url = "http://{}:{}/".format(HOST, httpd.server_address[1])

    count = len(load_store()["mails"])
    print("Mail Filter viewer running at {}".format(url))
    print("  {} message(s) in the store. Local only - nothing is exposed to the network.".format(count))
    print("  Press Ctrl+C to stop.")

    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
