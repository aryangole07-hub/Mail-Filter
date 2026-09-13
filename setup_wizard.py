#!/usr/bin/env python3
"""
setup_wizard.py - the click-Continue installer for Mail Filter.

Compiled to setup.exe by build_setup.py. It carries its own Python (PyInstaller
bundles one), the application source, and the Google OAuth client, so the person
running it needs nothing installed beforehand.

What it cannot do without them:
  * sign in to Google - OAuth consent is the user's to give, by design;
  * anything else. Every other step below is automatic.

Run with --dry-run to walk the screens without touching the machine.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import tkinter as tk
from tkinter import filedialog, ttk

APP_NAME = "Mail Filter"
INSTALL_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "MailFilter")
TASK_NAME = "MailFilterDigest"
ALERT_TASK_NAME = "MailFilterAlert"
ALERT_TIME = "20:00"
DIGEST_TIME = "07:55"
MODEL = "gemma3:4b"
VIEWER_URL = "http://127.0.0.1:8765/"

PYTHON_MIN = (3, 10)
PYTHON_INSTALLER = (
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe")
OLLAMA_INSTALLER = "https://ollama.com/download/OllamaSetup.exe"

# Files copied out of the bundle into the install directory.
APP_FILES = [
    "mail_filter.py", "viewer.py", "courses.py", "events.py", "qa.py",
    "alerts.py", "run_alert.ps1",
    "ui.html", "check_account.py", "run_digest.ps1", "view.ps1",
    "requirements.txt", "README.md",
]

DRY_RUN = "--dry-run" in sys.argv


def bundled(name):
    """Path to a file shipped inside the exe (or beside the script in dev)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def run(cmd, **kw):
    """Run a command with no console window popping up."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, creationflags=flags, capture_output=True,
                          text=True, **kw)


# ---------------------------------------------------------------------------
# The work, off the UI thread
# ---------------------------------------------------------------------------

class Installer:
    """Every step reports through `log`; any raise aborts with that message."""

    def __init__(self, log, progress):
        self.log = log
        self.progress = progress
        self.python = None

    # -- python ------------------------------------------------------------
    def find_python(self):
        for cmd in (["py", "-3"], ["python"], ["python3"]):
            try:
                out = run(cmd + ["-c", "import sys;print(sys.version_info[:2])"])
                if out.returncode == 0:
                    major, minor = eval(out.stdout.strip())
                    if (major, minor) >= PYTHON_MIN:
                        return cmd
            except (OSError, SyntaxError, ValueError):
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
            self.python = ["python"]
            return
        self.log("  Not found. Downloading Python 3.12 (about 25 MB)...")
        installer = os.path.join(os.environ.get("TEMP", "."), "python-setup.exe")
        self.download(PYTHON_INSTALLER, installer)
        self.log("  Installing Python (this takes a minute)...")
        run([installer, "/quiet", "InstallAllUsers=0", "PrependPath=1",
             "Include_test=0"])
        for _ in range(30):
            time.sleep(2)
            found = self.find_python()
            if found:
                self.python = found
                self.log("  Python installed.")
                return
        raise RuntimeError(
            "Python was installed but could not be started. Restart the "
            "computer and run this setup again.")

    # -- ollama ------------------------------------------------------------
    def ollama_up(self):
        try:
            with urllib.request.urlopen(
                    "http://localhost:11434/api/tags", timeout=4):
                return True
        except Exception:  # noqa: BLE001
            return False

    def ollama_exe(self):
        path = shutil.which("ollama")
        if path:
            return path
        guess = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             "Programs", "Ollama", "ollama.exe")
        return guess if os.path.exists(guess) else None

    def ensure_ollama(self):
        self.log("Looking for Ollama (the local AI engine)...")
        if DRY_RUN:
            self.log("  (dry run)")
            return
        if not self.ollama_exe():
            self.log("  Not found. Downloading Ollama (about 700 MB)...")
            installer = os.path.join(os.environ.get("TEMP", "."),
                                     "OllamaSetup.exe")
            self.download(OLLAMA_INSTALLER, installer)
            self.log("  Installing Ollama...")
            run([installer, "/SILENT", "/VERYSILENT", "/NORESTART"])
            for _ in range(30):
                time.sleep(2)
                if self.ollama_exe():
                    break
            else:
                raise RuntimeError(
                    "Ollama did not finish installing. Install it by hand from "
                    "ollama.com, then run this setup again.")
        self.log("  Ollama present.")

        if not self.ollama_up():
            self.log("  Starting the Ollama service...")
            exe = self.ollama_exe()
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen([exe, "serve"], creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(20):
                time.sleep(1)
                if self.ollama_up():
                    break
            else:
                raise RuntimeError("Ollama would not start.")
        self.log("  Ollama is running.")

    def ensure_model(self):
        self.log("Checking the language model ({})...".format(MODEL))
        if DRY_RUN:
            return
        try:
            with urllib.request.urlopen(
                    "http://localhost:11434/api/tags", timeout=10) as resp:
                tags = json.loads(resp.read())
            names = [m.get("name", "") for m in tags.get("models") or []]
            if MODEL in names or MODEL + ":latest" in names:
                self.log("  Already downloaded.")
                return
        except Exception:  # noqa: BLE001
            pass

        self.log("  Downloading the model (3.3 GB). This is the slow part -")
        self.log("  it can take 5 to 20 minutes on a normal connection.")
        exe = self.ollama_exe()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen([exe, "pull", MODEL], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                creationflags=flags, bufsize=1)
        last = 0
        for line in proc.stdout:
            line = line.strip()
            if line and time.time() - last > 3:
                self.log("    " + line[:70])
                last = time.time()
        if proc.wait() != 0:
            raise RuntimeError(
                "The model download failed. Check your internet connection and "
                "run this setup again - it will resume where it stopped.")
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
        missing = []
        for name in APP_FILES:
            src = bundled(name)
            if not os.path.exists(src):
                missing.append(name)
                continue
            shutil.copy2(src, os.path.join(INSTALL_DIR, name))
        if missing:
            raise RuntimeError(
                "This setup file is incomplete (missing {}). Download it "
                "again.".format(", ".join(missing)))

        creds = bundled("credentials.json")
        target = os.path.join(INSTALL_DIR, "credentials.json")
        if os.path.exists(creds):
            shutil.copy2(creds, target)
            self.log("  Google connection details included.")
        elif not os.path.exists(target):
            raise RuntimeError("MISSING_CREDENTIALS")

    def make_venv(self):
        self.log("Setting up the Python environment...")
        if DRY_RUN:
            return
        venv = os.path.join(INSTALL_DIR, ".venv")
        if not os.path.exists(os.path.join(venv, "Scripts", "python.exe")):
            out = run(self.python + ["-m", "venv", venv])
            if out.returncode != 0:
                raise RuntimeError("Could not create the Python environment:\n"
                                   + (out.stderr or "")[:300])
        py = os.path.join(venv, "Scripts", "python.exe")
        self.log("  Installing the pieces it needs...")
        out = run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                   "-r", os.path.join(INSTALL_DIR, "requirements.txt")])
        if out.returncode != 0:
            raise RuntimeError("Could not install dependencies:\n"
                               + (out.stderr or "")[:300])
        self.log("  Environment ready.")

    def venv_python(self):
        return os.path.join(INSTALL_DIR, ".venv", "Scripts", "python.exe")

    # -- google ------------------------------------------------------------
    def sign_in(self):
        """Run one fetch, which triggers the Google consent screen."""
        self.log("Opening Google sign-in in your browser...")
        self.log("")
        self.log("  Sign in with the account whose mail you want sorted.")
        self.log('  You will see "Google has not verified this app" - that is')
        self.log("  expected. Click Advanced, then Go to Mail Filter.")
        self.log("")
        if DRY_RUN:
            time.sleep(1)
            return
        proc = subprocess.Popen(
            [self.venv_python(), "mail_filter.py", "--backfill", "14"],
            cwd=INSTALL_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log("  " + line[:78])
        if proc.wait() != 0:
            raise RuntimeError(
                "Sign-in did not complete. Run the setup again and make sure "
                "you finish the Google screen in your browser.")
        if not os.path.exists(os.path.join(INSTALL_DIR, "token.json")):
            raise RuntimeError("Sign-in did not complete - no account was linked.")
        self.log("  Signed in.")

    # -- schedule ----------------------------------------------------------
    def schedule(self):
        self.log("Scheduling the daily digest for {}...".format(DIGEST_TIME))
        if DRY_RUN:
            return
        script = os.path.join(INSTALL_DIR, "run_digest.ps1")
        cmd = [
            "schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "DAILY",
            "/ST", DIGEST_TIME, "/TR",
            'powershell -NoProfile -ExecutionPolicy Bypass -File "{}"'.format(script),
        ]
        out = run(cmd)
        if out.returncode != 0:
            # Not fatal: everything still works when opened by hand.
            self.log("  Could not add the scheduled task; you can still open "
                     "the app any time.")
        else:
            self.log("  Scheduled.")

        self.log("Scheduling the evening reminder for {}...".format(ALERT_TIME))
        # Via the wrapper script: a bare "python.exe alerts.py" breaks in
        # schtasks the moment the install path contains a space.
        alert = run([
            "schtasks", "/Create", "/F", "/TN", ALERT_TASK_NAME, "/SC", "DAILY",
            "/ST", ALERT_TIME, "/TR",
            'powershell -NoProfile -ExecutionPolicy Bypass -File "{}"'.format(
                os.path.join(INSTALL_DIR, "run_alert.ps1")),
        ])
        self.log("  Scheduled." if alert.returncode == 0
                 else "  Could not add the reminder task.")

        # Open at logon, name the notifications, and register mailfilter: so
        # the button on a reminder opens the app window.
        self.log("Setting it to open when the PC starts...")
        autostart = run([
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            os.path.join(INSTALL_DIR, "install_autostart.ps1"),
        ])
        self.log("  Done." if autostart.returncode == 0
                 else "  Could not set it to open at startup; the desktop "
                      "shortcut still works.")

    def make_shortcut(self):
        if DRY_RUN:
            return
        try:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(desktop):
                return
            target = os.path.join(desktop, "Mail Filter.lnk")
            # run_viewer.ps1, not view.ps1: it opens the app window rather
            # than a console plus a browser tab, and reuses the window and
            # the server if they are already up.
            ps = (
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}');"
                "$s.TargetPath='powershell';"
                "$s.Arguments='-NoProfile -WindowStyle Hidden "
                "-ExecutionPolicy Bypass -File \"{}\" -Quiet';"
                "$s.WindowStyle=7;"
                "$s.WorkingDirectory='{}';$s.Save()"
            ).format(target, os.path.join(INSTALL_DIR, "run_viewer.ps1"), INSTALL_DIR)
            run(["powershell", "-NoProfile", "-Command", ps])
            self.log("  Desktop shortcut created.")
        except Exception:  # noqa: BLE001 - a shortcut is a nicety
            pass

    def start_viewer(self):
        self.log("Starting your mail page...")
        if DRY_RUN:
            return
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen([self.venv_python(), "viewer.py", "--no-browser"],
                         cwd=INSTALL_DIR, creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)

    def run_all(self):
        self.copy_app()
        self.ensure_python()
        self.make_venv()
        self.ensure_ollama()
        self.ensure_model()
        self.sign_in()
        self.schedule()
        self.make_shortcut()
        self.start_viewer()


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

BG = "#14161a"
FG = "#e8eaee"
MUTED = "#98a1ae"
ACCENT = "#7aa2ff"


class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME + " Setup")
        self.geometry("620x460")
        self.minsize(560, 420)
        self.configure(bg=BG)
        self.messages = queue.Queue()
        self.failed = None
        self.done = False
        self._build()
        self.show_welcome()
        self.after(100, self._drain)

    def _build(self):
        self.header = tk.Label(self, text=APP_NAME, bg=BG, fg=FG,
                               font=("Segoe UI", 20, "bold"))
        self.header.pack(anchor="w", padx=28, pady=(24, 2))
        self.sub = tk.Label(self, text="", bg=BG, fg=MUTED,
                            font=("Segoe UI", 10), justify="left", wraplength=540)
        self.sub.pack(anchor="w", padx=28)

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=28, pady=16)

        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)

        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=28, pady=(0, 22))
        self.button = tk.Button(footer, text="Continue", command=self.next,
                                bg=ACCENT, fg="#0e1013", relief="flat",
                                font=("Segoe UI", 11, "bold"),
                                padx=26, pady=9, cursor="hand2",
                                activebackground="#9dbaff")
        self.button.pack(side="right")
        self.secondary = tk.Button(footer, text="", command=lambda: None,
                                   bg=BG, fg=MUTED, relief="flat",
                                   font=("Segoe UI", 10), cursor="hand2")

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.secondary.pack_forget()

    def text(self, msg, size=11, color=FG, pad=(0, 6)):
        lbl = tk.Label(self.body, text=msg, bg=BG, fg=color,
                       font=("Segoe UI", size), justify="left", wraplength=540,
                       anchor="w")
        lbl.pack(anchor="w", pady=pad, fill="x")
        return lbl

    # -- screens -----------------------------------------------------------
    def show_welcome(self):
        self._clear()
        self.step = "welcome"
        self.sub.config(text="Sorts your college mail so nothing important gets buried.")
        self.text("This will set everything up for you:", 11, MUTED)
        for line in (
            "Install Python and the local AI engine, if you don't have them",
            "Download the AI model that reads your mail (3.3 GB)",
            "Sign you in to Google, read-only",
            "Schedule a digest every morning at 7:55",
            "Open your mail page in the browser",
        ):
            self.text("   •   " + line, 10)
        self.text("")
        self.text("Your mail never leaves this computer. Everything runs here, "
                  "and nothing is uploaded anywhere.", 10, MUTED)
        self.text("Set aside about 15 minutes, mostly waiting on downloads.",
                  10, MUTED)
        self.button.config(text="Continue", state="normal")

    def show_working(self):
        self._clear()
        self.step = "working"
        self.sub.config(text="Setting things up. You can leave this running.")
        self.log_box = tk.Text(self.body, bg="#0e1013", fg=MUTED, relief="flat",
                               font=("Consolas", 9), wrap="word",
                               insertbackground=FG, height=14)
        self.log_box.pack(fill="both", expand=True)
        self.log_box.config(state="disabled")
        self.bar.pack(fill="x", padx=28, pady=(0, 10), before=self.body)
        self.button.config(text="Working…", state="disabled")
        threading.Thread(target=self._work, daemon=True).start()

    def show_creds(self):
        self._clear()
        self.step = "creds"
        self.sub.config(text="One file is missing.")
        self.text("This copy of the setup did not come with its Google "
                  "connection file, so it cannot sign you in.", 11)
        self.text("Ask whoever sent you this setup for the original file, or "
                  "pick a credentials.json yourself if you have one.", 10, MUTED)
        self.button.config(text="Choose file…", state="normal")

    def show_done(self):
        self._clear()
        self.step = "done"
        self.done = True
        self.bar.pack_forget()
        self.sub.config(text="You're set up.")
        self.text("Your mail page is running at:", 11, MUTED)
        link = tk.Label(self.body, text=VIEWER_URL, bg=BG, fg=ACCENT,
                        font=("Segoe UI", 16, "bold"), cursor="hand2")
        link.pack(anchor="w", pady=(2, 14))
        link.bind("<Button-1>", lambda e: webbrowser.open(VIEWER_URL))
        self.text("Open that in any browser on this computer. It only works "
                  "here — the page is not on the internet.", 10, MUTED)
        self.text("")
        self.text("A digest is prepared every morning at 7:55, and you get a "
                  "reminder each evening about anything happening the next "
                  "day. There is a Mail Filter shortcut on your desktop to "
                  "open the page again later.", 10, MUTED)
        self.button.config(text="Open my mail", state="normal")

    def show_failed(self, message):
        self._clear()
        self.step = "failed"
        self.bar.pack_forget()
        self.sub.config(text="Setup could not finish.")
        self.text(message, 11)
        self.text("")
        self.text("Nothing has been broken. You can close this and run the "
                  "setup again — it picks up where it left off.", 10, MUTED)
        self.button.config(text="Close", state="normal")

    # -- plumbing ----------------------------------------------------------
    def log(self, msg):
        self.messages.put(("log", msg))

    def progress(self, pct):
        self.messages.put(("progress", pct))

    def _work(self):
        installer = Installer(self.log, self.progress)
        try:
            installer.run_all()
            self.messages.put(("done", None))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.messages.put(("failed", str(exc)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log" and hasattr(self, "log_box"):
                    self.log_box.config(state="normal")
                    self.log_box.insert("end", payload + "\n")
                    self.log_box.see("end")
                    self.log_box.config(state="disabled")
                elif kind == "progress":
                    self.bar["value"] = payload
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
            self.show_working()
        elif self.step == "creds":
            path = filedialog.askopenfilename(
                title="Choose credentials.json",
                filetypes=[("Google credentials", "*.json"), ("All files", "*.*")])
            if path:
                os.makedirs(INSTALL_DIR, exist_ok=True)
                shutil.copy2(path, os.path.join(INSTALL_DIR, "credentials.json"))
                self.show_working()
        elif self.step == "done":
            webbrowser.open(VIEWER_URL)
            self.destroy()
        else:
            self.destroy()


def main():
    Wizard().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
