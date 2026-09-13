#!/usr/bin/env python3
"""
setup_wizard.py - one guided install of Mail Filter, on Windows and macOS.

On Windows it is compiled to setup.exe (build_setup.py), carrying its own
Python, the app and the Google OAuth client. On a Mac it is started by
"Install Mail Filter.command" from MailFilter-mac.zip, which finds or installs a
suitable Python first.

The student types two things - their name and BITS ID - and picks one picture:
a screenshot of their ERP weekly timetable. Everything else is automatic:
Python environment, Ollama and the model, reading the timetable, Google sign-in
(the one step that needs a click, in the browser, because consent is the
student's to give), the daily digest, the evening reminder, opening at login, a
desktop shortcut, and opening the app at the end.

Anything that is missing or unreadable - an ID that does not look like a BITS
ID, no timetable picture, a timetable box that could not be read - is shown as
a warning with "Continue regardless", never a dead end.

Run with --dry-run to walk the screens without touching the machine.
"""

import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser

import tkinter as tk
from tkinter import filedialog, ttk

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

APP_NAME = "Mail Filter"
if IS_WINDOWS:
    INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "MailFilter")
elif IS_MAC:
    INSTALL_DIR = os.path.join(os.path.expanduser("~/Library/Application Support"), "MailFilter")
else:
    INSTALL_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                               "MailFilter")
TASK_NAME = "MailFilterDigest"
ALERT_TASK_NAME = "MailFilterAlert"
ALERT_TIME = "20:00"
DIGEST_TIME = "07:55"
MODEL = "gemma3:4b"
VIEWER_URL = "http://127.0.0.1:8765/"
OLLAMA_API = "http://localhost:11434"

PYTHON_MIN = (3, 10)
PYTHON_INSTALLER = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
OLLAMA_INSTALLER_WINDOWS = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_MAC_ZIP = "https://ollama.com/download/Ollama-darwin.zip"

# Copied into the install directory on every system.
APP_FILES = [
    "mail_filter.py", "viewer.py", "courses.py", "events.py", "qa.py", "alerts.py",
    "people.py", "profile.py", "attachments.py", "user_notes.py", "calendar_store.py",
    "conflicts.py", "todo_store.py", "marks.py", "timetable_import.py", "platforms.py",
    "ui.html", "check_account.py", "requirements.txt", "README.md", "SETUP-FOR-FRIENDS.md",
]
# Launchers for one system only. install_autostart.ps1 is what makes Windows
# open the app at logon; install_autostart_mac.sh does the same on a Mac.
WINDOWS_FILES = ["run_digest.ps1", "run_alert.ps1", "run_viewer.ps1", "view.ps1",
                 "notify.ps1", "install_autostart.ps1"]
UNIX_FILES = ["run_digest.sh", "run_alert.sh", "run_viewer.sh", "install_autostart_mac.sh"]

# A BITS campus ID: 2025A7PS0001H. The mail login is f + year + the last four
# digits (2025B3PS0420H -> f20250420), at the campus's own domain.
ID_RE = re.compile(r"^(20\d\d)([A-Z0-9]{4})(\d{4})([HPGDU])$")
CAMPUS_DOMAINS = {"H": "hyderabad.bits-pilani.ac.in", "P": "pilani.bits-pilani.ac.in",
                  "G": "goa.bits-pilani.ac.in", "D": "dubai.bits-pilani.ac.in",
                  "U": "dubai.bits-pilani.ac.in"}
CAMPUS_NAMES = {"H": "BITS Pilani, Hyderabad Campus", "P": "BITS Pilani, Pilani Campus",
                "G": "BITS Pilani, K K Birla Goa Campus", "D": "BITS Pilani, Dubai Campus",
                "U": "BITS Pilani, Dubai Campus"}

DRY_RUN = "--dry-run" in sys.argv


def bundled(name):
    """Path to a file shipped with the installer (inside the exe, or beside it)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def run(cmd, **kw):
    """Run a command with no console window popping up."""
    return subprocess.run(cmd, creationflags=no_window(), capture_output=True,
                          text=True, **kw)


def details_from_id(campus_id):
    """{"email_id", "email", "campus", "batch"} worked out from a BITS ID."""
    match = ID_RE.match((campus_id or "").strip().upper())
    if not match:
        return {}
    year, _, serial, campus = match.groups()
    login = "f{}{}".format(year, serial)
    return {"email_id": login, "email": "{}@{}".format(login, CAMPUS_DOMAINS[campus]),
            "campus": CAMPUS_NAMES[campus], "batch": year}


def check_details(name, campus_id):
    """Plain-English problems with what was typed. Empty list = fine."""
    problems = []
    if not (name or "").strip():
        problems.append("Your name is empty.")
    if not (campus_id or "").strip():
        problems.append("Your BITS ID is empty. Exam seating plans are matched by it.")
    elif not ID_RE.match(campus_id.strip().upper()):
        problems.append("Your BITS ID does not look complete - it should look like "
                        "2025A7PS0001H. Exam seating plans are matched by it.")
    return problems


# ---------------------------------------------------------------------------
# The work, off the UI thread
# ---------------------------------------------------------------------------

class Installer:
    """Every step reports through `log`; any raise aborts with that message."""

    def __init__(self, log, progress, details, timetable_picture, ask_about_timetable):
        self.log = log
        self.progress = progress
        self.details = details
        self.picture = timetable_picture
        self.ask_about_timetable = ask_about_timetable
        self.python = None

    # -- python ------------------------------------------------------------
    def find_python(self):
        if not IS_WINDOWS:
            return [sys.executable]  # the .command already chose a suitable one
        for cmd in (["py", "-3"], ["python"], ["python3"]):
            try:
                out = run(cmd + ["-c", "import sys;print(sys.version_info[0], sys.version_info[1])"])
                if out.returncode == 0:
                    major, minor = (int(x) for x in out.stdout.split())
                    if (major, minor) >= PYTHON_MIN:
                        return cmd
            except (OSError, ValueError):
                continue
        return None

    def ensure_python(self):
        self.log("Looking for Python...")
        found = self.find_python()
        if found:
            self.python = found
            self.log("  Python found.")
            return
        if DRY_RUN:
            self.python = [sys.executable]
            return
        self.log("  Not found. Downloading Python 3.12 (about 25 MB)...")
        installer = os.path.join(tempfile.gettempdir(), "python-setup.exe")
        self.download(PYTHON_INSTALLER, installer)
        self.log("  Installing Python (this takes a minute)...")
        run([installer, "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0"])
        for _ in range(30):
            time.sleep(2)
            found = self.find_python()
            if found:
                self.python = found
                self.log("  Python installed.")
                return
        raise RuntimeError("Python was installed but could not be started. Restart the "
                           "computer and run this setup again.")

    def venv_python(self):
        if IS_WINDOWS:
            return os.path.join(INSTALL_DIR, ".venv", "Scripts", "python.exe")
        return os.path.join(INSTALL_DIR, ".venv", "bin", "python")

    def make_venv(self):
        self.log("Setting up the app's own Python environment...")
        if DRY_RUN:
            return
        venv = os.path.join(INSTALL_DIR, ".venv")
        if not os.path.exists(self.venv_python()):
            out = run(self.python + ["-m", "venv", venv])
            if out.returncode != 0:
                raise RuntimeError("Could not create the Python environment:\n"
                                   + (out.stderr or "")[:300])
        self.log("  Installing the pieces it needs (a few minutes)...")
        out = run([self.venv_python(), "-m", "pip", "install", "--quiet",
                   "--disable-pip-version-check", "-r",
                   os.path.join(INSTALL_DIR, "requirements.txt")])
        if out.returncode != 0:
            raise RuntimeError("Could not install what the app needs:\n" + (out.stderr or "")[:300])
        self.log("  Environment ready.")

    # -- ollama ------------------------------------------------------------
    def ollama_up(self):
        try:
            with urllib.request.urlopen(OLLAMA_API + "/api/tags", timeout=4):
                return True
        except Exception:  # noqa: BLE001
            return False

    def ollama_app(self):
        """Where Ollama is installed, or None."""
        if IS_MAC:
            for root in ("/Applications", os.path.expanduser("~/Applications")):
                path = os.path.join(root, "Ollama.app")
                if os.path.isdir(path):
                    return path
            return shutil.which("ollama")
        path = shutil.which("ollama")
        if path:
            return path
        guess = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        return guess if os.path.exists(guess) else None

    def start_ollama(self):
        where = self.ollama_app()
        if not where:
            return
        if IS_MAC and where.endswith(".app"):
            subprocess.Popen(["open", "-g", "-a", where])
        else:
            subprocess.Popen([where, "serve"], creationflags=no_window(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def ensure_ollama(self):
        self.log("Looking for Ollama (the AI engine that runs on this computer)...")
        if DRY_RUN:
            self.log("  (dry run)")
            return
        if not self.ollama_app():
            if IS_WINDOWS:
                self.log("  Not found. Downloading Ollama (about 700 MB)...")
                installer = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
                self.download(OLLAMA_INSTALLER_WINDOWS, installer)
                self.log("  Installing Ollama...")
                run([installer, "/SILENT", "/VERYSILENT", "/NORESTART"])
            elif IS_MAC:
                self.log("  Not found. Downloading Ollama for Mac (about 200 MB)...")
                archive = os.path.join(tempfile.gettempdir(), "Ollama-darwin.zip")
                self.download(OLLAMA_MAC_ZIP, archive)
                target = os.path.expanduser("~/Applications")
                os.makedirs(target, exist_ok=True)
                self.log("  Installing Ollama into your Applications folder...")
                # ditto keeps the app's signature and permissions intact.
                out = run(["ditto", "-xk", archive, target])
                if out.returncode != 0:
                    raise RuntimeError("Could not unpack Ollama. Install it from "
                                       "ollama.com, then run this setup again.")
            else:
                raise RuntimeError("Install Ollama from ollama.com, then run this setup again.")
            for _ in range(30):
                time.sleep(2)
                if self.ollama_app():
                    break
            else:
                raise RuntimeError("Ollama did not finish installing. Install it by hand "
                                   "from ollama.com, then run this setup again.")
        self.log("  Ollama is installed.")

        if not self.ollama_up():
            self.log("  Starting Ollama...")
            self.start_ollama()
            for _ in range(40):
                time.sleep(1)
                if self.ollama_up():
                    break
            else:
                raise RuntimeError("Ollama would not start. Open the Ollama app once, "
                                   "then run this setup again.")
        self.log("  Ollama is running.")

    def ensure_model(self):
        self.log("Checking the AI model ({})...".format(MODEL))
        if DRY_RUN:
            return
        try:
            with urllib.request.urlopen(OLLAMA_API + "/api/tags", timeout=10) as resp:
                names = [m.get("name", "") for m in json.loads(resp.read()).get("models") or []]
            if MODEL in names or MODEL + ":latest" in names:
                self.log("  Already downloaded.")
                return
        except Exception:  # noqa: BLE001
            pass
        self.log("  Downloading the model (3.3 GB). This is the slow part - it can")
        self.log("  take 5 to 20 minutes. It resumes if the connection drops.")
        request = urllib.request.Request(
            OLLAMA_API + "/api/pull", data=json.dumps({"model": MODEL}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        last = 0
        with urllib.request.urlopen(request, timeout=3600) as resp:
            for raw in resp:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("error"):
                    raise RuntimeError("The model download failed ({}). Check the internet "
                                       "connection and run this setup again.".format(event["error"]))
                if event.get("total"):
                    self.progress(int(100 * (event.get("completed") or 0) / event["total"]))
                if time.time() - last > 5 and event.get("status"):
                    self.log("    " + event["status"][:70])
                    last = time.time()
        self.progress(0)
        self.log("  Model ready.")

    # -- app ---------------------------------------------------------------
    def download(self, url, dest):
        def hook(count, block, total):
            if total > 0:
                self.progress(min(100, count * block * 100 // total))
        urllib.request.urlretrieve(url, dest, hook)
        self.progress(0)

    def copy_app(self):
        self.log("Installing {} into:".format(APP_NAME))
        self.log("  " + INSTALL_DIR)
        if DRY_RUN:
            return
        os.makedirs(INSTALL_DIR, exist_ok=True)
        wanted = APP_FILES + (WINDOWS_FILES if IS_WINDOWS else UNIX_FILES)
        missing = []
        for name in wanted:
            src = bundled(name)
            if not os.path.exists(src):
                missing.append(name)
                continue
            dest = os.path.join(INSTALL_DIR, name)
            shutil.copy2(src, dest)
            if name.endswith(".sh"):
                os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if missing:
            raise RuntimeError("This setup is incomplete (missing {}). Download it again."
                               .format(", ".join(missing)))

        creds = bundled("credentials.json")
        target = os.path.join(INSTALL_DIR, "credentials.json")
        if os.path.exists(creds):
            shutil.copy2(creds, target)
            self.log("  Google connection details included.")
        elif not os.path.exists(target):
            raise RuntimeError("MISSING_CREDENTIALS")

    def write_profile(self):
        self.log("Saving your details...")
        if DRY_RUN:
            return
        campus_id = (self.details.get("campus_id") or "").strip().upper()
        profile = {"name": (self.details.get("name") or "").strip(), "campus_id": campus_id}
        profile.update(details_from_id(campus_id))
        path = os.path.join(INSTALL_DIR, "profile.json")
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    existing = json.load(fh) or {}
            except (OSError, ValueError):
                existing = {}
        existing.update({k: v for k, v in profile.items() if v})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2, ensure_ascii=False)
        self.log("  Saved.")

    def import_timetable(self):
        """Read the timetable picture; warn and ask when anything is missing."""
        while True:
            if not self.picture:
                self.log("No timetable picture - skipping. Courses will come from your mail.")
                return
            self.log("Reading your timetable picture (a few minutes - each class box")
            self.log("is read by the AI on this computer)...")
            if DRY_RUN:
                warnings = ["Tuesday 10:00 (ECON F214): could not read the room. (dry run)"]
            else:
                report = os.path.join(tempfile.gettempdir(), "mailfilter-timetable-report.json")
                proc = subprocess.Popen(
                    [self.venv_python(), "timetable_import.py", self.picture, "--save",
                     "--report", report],
                    cwd=INSTALL_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, creationflags=no_window())
                for line in proc.stdout:
                    line = line.rstrip()
                    match = re.search(r"read box (\d+)/(\d+)", line)
                    if match:
                        self.progress(int(100 * int(match.group(1)) / int(match.group(2))))
                    elif line and not line.startswith("WARNING"):
                        self.log("  " + line[:78])
                proc.wait()
                self.progress(0)
                try:
                    with open(report, encoding="utf-8") as fh:
                        summary = json.load(fh)
                except (OSError, ValueError):
                    summary = {"classes": 0, "warnings": ["The picture could not be read at all."]}
                warnings = list(summary.get("warnings") or [])
                if not summary.get("classes"):
                    warnings.insert(0, "No classes could be read from this picture.")
                self.log("  Read {} classes.".format(summary.get("classes", 0)))
            if not warnings:
                self.log("  Timetable read with nothing missing.")
                return
            decision = self.ask_about_timetable(warnings)
            if decision == "continue":
                self.log("  Continuing with what could be read.")
                return
            self.picture = decision  # a newly chosen picture: read that one

    def sign_in(self):
        """Run one fetch, which opens the Google consent screen."""
        self.log("Opening Google sign-in in your browser...")
        self.log("")
        self.log("  Sign in with your BITS mail account.")
        self.log('  If you see "Google hasn\'t verified this app", click Advanced,')
        self.log("  then Go to Mail Filter. It only ever reads your mail.")
        self.log("")
        if DRY_RUN:
            time.sleep(1)
            return
        proc = subprocess.Popen(
            [self.venv_python(), "mail_filter.py", "--backfill", "14"],
            cwd=INSTALL_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=no_window())
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log("  " + line[:78])
        if proc.wait() != 0 or not os.path.exists(os.path.join(INSTALL_DIR, "token.json")):
            raise RuntimeError("Sign-in did not complete. Run the setup again and finish the "
                               "Google screen in your browser.")
        self.log("  Signed in. Your first digest is ready.")
        run([self.venv_python(), "profile.py", "--detect"], cwd=INSTALL_DIR)

    # -- schedule, shortcut, open ------------------------------------------
    def schedule(self):
        self.log("Setting up the 7:55 digest, the 8 pm reminder and opening at login...")
        if DRY_RUN:
            return
        if IS_WINDOWS:
            for name, when, script in ((TASK_NAME, DIGEST_TIME, "run_digest.ps1"),
                                       (ALERT_TASK_NAME, ALERT_TIME, "run_alert.ps1")):
                out = run(["schtasks", "/Create", "/F", "/TN", name, "/SC", "DAILY", "/ST", when,
                           "/TR", 'powershell -NoProfile -ExecutionPolicy Bypass -File "{}"'.format(
                               os.path.join(INSTALL_DIR, script))])
                if out.returncode != 0:
                    self.log("  Could not schedule {}; the app still works when opened.".format(name))
            autostart = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             os.path.join(INSTALL_DIR, "install_autostart.ps1")])
            self.log("  Done." if autostart.returncode == 0
                     else "  Could not set it to open at startup; the desktop shortcut still works.")
        elif IS_MAC:
            out = run(["/bin/bash", os.path.join(INSTALL_DIR, "install_autostart_mac.sh")])
            self.log("  Done." if out.returncode == 0
                     else "  Could not set up the background jobs; the shortcut still works.")
        else:
            self.log("  (Scheduling is set up automatically on Windows and macOS only.)")

    def make_shortcut(self):
        if DRY_RUN:
            return
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            return
        try:
            if IS_WINDOWS:
                target = os.path.join(desktop, "Mail Filter.lnk")
                ps = (
                    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}');"
                    "$s.TargetPath='powershell';"
                    "$s.Arguments='-NoProfile -WindowStyle Hidden "
                    "-ExecutionPolicy Bypass -File \"{}\" -Quiet';"
                    "$s.WindowStyle=7;"
                    "$s.WorkingDirectory='{}';$s.Save()"
                ).format(target, os.path.join(INSTALL_DIR, "run_viewer.ps1"), INSTALL_DIR)
                run(["powershell", "-NoProfile", "-Command", ps])
            else:
                target = os.path.join(desktop, "Mail Filter.command")
                with open(target, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write('#!/bin/bash\nexec /bin/bash "{}" --quiet\n'.format(
                        os.path.join(INSTALL_DIR, "run_viewer.sh")))
                os.chmod(target, 0o755)
            self.log("  Desktop shortcut created.")
        except Exception:  # noqa: BLE001 - a shortcut is a nicety
            pass

    def open_app(self):
        self.log("Opening Mail Filter...")
        if DRY_RUN:
            return
        if IS_WINDOWS:
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                              "-ExecutionPolicy", "Bypass", "-File",
                              os.path.join(INSTALL_DIR, "run_viewer.ps1"), "-Quiet"],
                             cwd=INSTALL_DIR, creationflags=no_window())
        else:
            subprocess.Popen(["/bin/bash", os.path.join(INSTALL_DIR, "run_viewer.sh"), "--quiet"],
                             cwd=INSTALL_DIR)
        time.sleep(3)

    def run_all(self):
        self.copy_app()
        self.ensure_python()
        self.make_venv()
        self.ensure_ollama()
        self.ensure_model()
        self.write_profile()
        self.import_timetable()
        self.sign_in()
        self.schedule()
        self.make_shortcut()
        self.open_app()


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

BG = "#14161a"
PANEL = "#1c1f25"
FG = "#e8eaee"
MUTED = "#98a1ae"
ACCENT = "#7aa2ff"
WARN = "#fbbf24"


class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME + " Setup")
        self.geometry("660x580")
        self.minsize(600, 520)
        self.configure(bg=BG)
        self.messages = queue.Queue()
        self.details = {"name": "", "campus_id": ""}
        self.picture = ""
        self.acknowledged = set()
        self.answer = queue.Queue()
        self._build()
        self.show_welcome()
        self.after(100, self._drain)

    def _build(self):
        self.header = tk.Label(self, text=APP_NAME, bg=BG, fg=FG, font=("Segoe UI", 20, "bold"))
        self.header.pack(anchor="w", padx=28, pady=(24, 2))
        self.sub = tk.Label(self, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10),
                            justify="left", wraplength=580)
        self.sub.pack(anchor="w", padx=28)
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=28, pady=16)
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=28, pady=(0, 22))
        self.button = tk.Button(footer, text="Continue", command=self.next, bg=ACCENT,
                                fg="#0e1013", relief="flat", font=("Segoe UI", 11, "bold"),
                                padx=26, pady=9, cursor="hand2", activebackground="#9dbaff")
        self.button.pack(side="right")
        self.secondary = tk.Button(footer, text="", bg=PANEL, fg=FG, relief="flat",
                                   font=("Segoe UI", 10), padx=16, pady=8, cursor="hand2")

    def _clear(self):
        for widget in self.body.winfo_children():
            widget.destroy()
        self.secondary.pack_forget()

    def text(self, msg, size=11, color=FG, pad=(0, 6)):
        label = tk.Label(self.body, text=msg, bg=BG, fg=color, font=("Segoe UI", size),
                         justify="left", wraplength=590, anchor="w")
        label.pack(anchor="w", pady=pad, fill="x")
        return label

    def entry(self, label, value):
        self.text(label, 10, MUTED, (8, 2))
        var = tk.StringVar(value=value)
        field = tk.Entry(self.body, textvariable=var, bg=PANEL, fg=FG, insertbackground=FG,
                         relief="flat", font=("Segoe UI", 12))
        field.pack(fill="x", ipady=7)
        return var

    # -- screens -----------------------------------------------------------
    def show_welcome(self):
        self._clear()
        self.step = "welcome"
        self.sub.config(text="Sorts your college mail so nothing important gets buried.")
        self.text("Setup takes about 20-30 minutes, mostly downloading. You will:", 11, MUTED)
        for line in ("Type your name and BITS ID",
                     "Pick a screenshot of your ERP weekly timetable",
                     "Sign in to your BITS mail once, in the browser"):
            self.text("   •   " + line, 11)
        self.text("")
        self.text("Everything else happens by itself. Your mail never leaves this computer - "
                  "the AI runs here, and nothing is uploaded anywhere.", 10, MUTED)
        self.button.config(text="Start", state="normal")

    def show_details(self, problems=None):
        self._clear()
        self.step = "details"
        self.sub.config(text="Step 1 of 3 - about you")
        self.name_var = self.entry("Your name", self.details["name"])
        self.id_var = self.entry("Your BITS ID (for example 2025A7PS0001H)",
                                 self.details["campus_id"])
        self.text("Your college email is worked out from your ID. Nothing here is sent "
                  "anywhere - it stays on this computer.", 9, MUTED, (8, 0))
        if problems:
            for problem in problems:
                self.text("⚠  " + problem, 10, WARN, (6, 0))
            self.secondary.config(text="Fix it", command=lambda: self.name_var.set(self.name_var.get()))
            self.button.config(text="Continue regardless")
        else:
            self.button.config(text="Continue")

    def show_timetable(self, warning=None):
        self._clear()
        self.step = "timetable"
        self.sub.config(text="Step 2 of 3 - your timetable")
        self.text("Take a screenshot of your weekly class schedule from ERP:", 11)
        for line in ("Open your weekly class schedule in ERP.",
                     "Tick EVERY box under Display Options (course name, room, instructor, "
                     "times...).",
                     "Screenshot the whole week so every class box is visible "
                     "(Windows: Win + Shift + S.  Mac: Cmd + Shift + 4).",
                     "Save it, then choose it below."):
            self.text("   •   " + line, 10)
        chosen = os.path.basename(self.picture) if self.picture else "No picture chosen yet"
        self.text("\U0001F5BC  " + chosen, 11, ACCENT if self.picture else MUTED, (12, 0))
        self.secondary.config(text="Choose picture…", command=self.choose_picture)
        self.secondary.pack(side="right", padx=(0, 10))
        if warning:
            self.text("⚠  " + warning, 10, WARN, (10, 0))
            self.button.config(text="Continue regardless")
        else:
            self.button.config(text="Continue")

    def choose_picture(self):
        path = filedialog.askopenfilename(
            title="Choose your timetable screenshot",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if path:
            self.picture = path
            self.acknowledged.discard("timetable")
            self.show_timetable()

    def show_ready(self):
        self._clear()
        self.step = "ready"
        self.sub.config(text="Step 3 of 3 - install")
        self.text("Ready. Press Install and leave this window open.", 11)
        self.text("Near the start your browser opens the Google sign-in: sign in with your BITS "
                  "mail account. Everything else is automatic, and Mail Filter opens by itself "
                  "at the end.", 10, MUTED)
        self.button.config(text="Install", state="normal")

    def show_working(self):
        self._clear()
        self.step = "working"
        self.sub.config(text="Setting things up. You can leave this running.")
        self.log_box = tk.Text(self.body, bg="#0e1013", fg=MUTED, relief="flat",
                               font=("Consolas", 9), wrap="word", insertbackground=FG, height=16)
        self.log_box.pack(fill="both", expand=True)
        self.log_box.config(state="disabled")
        self.bar.pack(fill="x", padx=28, pady=(0, 10), before=self.body)
        self.button.config(text="Working…", state="disabled")
        threading.Thread(target=self._work, daemon=True).start()

    def show_timetable_warnings(self, warnings):
        self._clear()
        self.step = "timetable-warnings"
        self.sub.config(text="Some of your timetable could not be read.")
        self.text("These details are missing:", 11)
        box = tk.Text(self.body, bg=PANEL, fg=WARN, relief="flat", font=("Segoe UI", 10),
                      wrap="word", height=9)
        box.insert("end", "\n".join("• " + w for w in warnings))
        box.config(state="disabled")
        box.pack(fill="x", pady=(4, 8))
        self.text("You can continue with what was read (missing details simply will not show "
                  "in the app), or choose a clearer screenshot with every Display Option "
                  "ticked.", 10, MUTED)
        self.secondary.config(text="Choose another picture…", command=self._retry_picture)
        self.secondary.pack(side="right", padx=(0, 10))
        self.button.config(text="Continue regardless", state="normal")

    def _retry_picture(self):
        path = filedialog.askopenfilename(
            title="Choose your timetable screenshot",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if path:
            self._resume(path)

    def _resume(self, decision):
        self.show_working_again()
        self.answer.put(decision)

    def show_working_again(self):
        self._clear()
        self.step = "working"
        self.sub.config(text="Setting things up. You can leave this running.")
        self.log_box = tk.Text(self.body, bg="#0e1013", fg=MUTED, relief="flat",
                               font=("Consolas", 9), wrap="word", height=16)
        self.log_box.pack(fill="both", expand=True)
        self.log_box.config(state="disabled")
        self.button.config(text="Working…", state="disabled")

    def show_creds(self):
        self._clear()
        self.step = "creds"
        self.sub.config(text="One file is missing.")
        self.text("This copy of the setup did not come with its Google connection file, so "
                  "it cannot sign you in.", 11)
        self.text("Ask whoever sent you this setup for the original file, or pick a "
                  "credentials.json yourself if you have one.", 10, MUTED)
        self.button.config(text="Choose file…", state="normal")

    def show_done(self):
        self._clear()
        self.step = "done"
        self.bar.pack_forget()
        self.sub.config(text="You're set up.")
        self.text("Mail Filter is open. From now on it:", 11)
        for line in ("opens by itself when you start your computer",
                     "prepares a digest of your mail every morning at 7:55",
                     "reminds you at 8 pm about anything happening tomorrow"):
            self.text("   •   " + line, 10)
        self.text("")
        self.text("There is a Mail Filter shortcut on your desktop to open it again later. "
                  "It only works on this computer - nothing is on the internet.", 10, MUTED)
        self.button.config(text="Finish", state="normal")

    def show_failed(self, message):
        self._clear()
        self.step = "failed"
        self.bar.pack_forget()
        self.sub.config(text="Setup could not finish.")
        self.text(message, 11)
        self.text("")
        self.text("Nothing has been broken. Close this and run the setup again - it picks up "
                  "where it left off.", 10, MUTED)
        self.button.config(text="Close", state="normal")

    # -- plumbing ----------------------------------------------------------
    def log(self, msg):
        self.messages.put(("log", msg))

    def progress(self, pct):
        self.messages.put(("progress", pct))

    def ask_about_timetable(self, warnings):
        """Called from the worker thread: show the warnings, wait for a decision."""
        self.messages.put(("ask", warnings))
        return self.answer.get()

    def _work(self):
        installer = Installer(self.log, self.progress, self.details, self.picture,
                              self.ask_about_timetable)
        try:
            installer.run_all()
            self.messages.put(("done", None))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.messages.put(("failed", str(exc)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log" and hasattr(self, "log_box") and self.log_box.winfo_exists():
                    self.log_box.config(state="normal")
                    self.log_box.insert("end", payload + "\n")
                    self.log_box.see("end")
                    self.log_box.config(state="disabled")
                elif kind == "progress":
                    self.bar["value"] = payload
                elif kind == "ask":
                    self.show_timetable_warnings(payload)
                elif kind == "done":
                    self.show_done()
                elif kind == "failed":
                    if payload == "MISSING_CREDENTIALS":
                        self.show_creds()
                    else:
                        self.show_failed(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def next(self):
        if self.step == "welcome":
            self.show_details()
        elif self.step == "details":
            self.details = {"name": self.name_var.get().strip(),
                            "campus_id": self.id_var.get().strip().upper()}
            problems = check_details(self.details["name"], self.details["campus_id"])
            if problems and "details" not in self.acknowledged:
                self.acknowledged.add("details")
                self.show_details(problems)
            else:
                self.show_timetable()
        elif self.step == "timetable":
            if not self.picture and "timetable" not in self.acknowledged:
                self.acknowledged.add("timetable")
                self.show_timetable("Without a timetable picture, your classes, rooms and "
                                    "clashes will not appear. You can still continue.")
            else:
                self.show_ready()
        elif self.step == "ready":
            self.show_working()
        elif self.step == "timetable-warnings":
            self._resume("continue")
        elif self.step == "creds":
            path = filedialog.askopenfilename(
                title="Choose credentials.json",
                filetypes=[("Google credentials", "*.json"), ("All files", "*.*")])
            if path:
                os.makedirs(INSTALL_DIR, exist_ok=True)
                shutil.copy2(path, os.path.join(INSTALL_DIR, "credentials.json"))
                self.show_working()
        else:
            self.destroy()


def main():
    Wizard().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
