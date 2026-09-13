#!/usr/bin/env python3
"""
platforms.py - the few things Windows and macOS do differently.

Everything else in Mail Filter is plain Python and runs anywhere. What is not:

  * where the app installs (%LOCALAPPDATA% / ~/Library/Application Support);
  * starting a background process without a console window;
  * opening the site as an app window (Edge/Chrome --app, or on a Mac Chrome,
    Edge or Brave with --app; the default browser when none is installed);
  * a reminder that stays on screen until it is clicked (a "reminder" toast on
    Windows via notify.ps1; a macOS alert dialog; a critical notify-send on
    Linux);
  * starting Ollama if it is not running;
  * running things on a schedule and at login (Task Scheduler on Windows,
    LaunchAgents on macOS - written here with plistlib, so they can be built
    and checked on any machine).

    python platforms.py install-launch-agents [install dir]
    python platforms.py uninstall-launch-agents
"""

import os
import plistlib
import shutil
import subprocess
import sys
import webbrowser

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

APP_NAME = "Mail Filter"
VIEWER_URL = "http://127.0.0.1:8765/"
LAUNCH_AGENT_PREFIX = "com.mailfilter."
DIGEST_TIME = (7, 55)
ALERT_TIME = (20, 0)
LOGIN_DELAY_SECONDS = 30

MAC_APP_BROWSERS = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Vivaldi"]


def no_window_flags():
    """creationflags that keep a console window from flashing up on Windows."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def app_data_dir(name="MailFilter"):
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), name)
    if IS_MAC:
        return os.path.join(os.path.expanduser("~/Library/Application Support"), name)
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), name)


def venv_python(install_dir):
    if IS_WINDOWS:
        return os.path.join(install_dir, ".venv", "Scripts", "python.exe")
    return os.path.join(install_dir, ".venv", "bin", "python")


# ---------------------------------------------------------------------------
# The app window
# ---------------------------------------------------------------------------

def find_app_browser(mac=None, windows=None):
    """A browser that can open a site as an app window, or None."""
    mac = IS_MAC if mac is None else mac
    windows = IS_WINDOWS if windows is None else windows
    if mac:
        for name in MAC_APP_BROWSERS:
            for root in ("/Applications", os.path.expanduser("~/Applications")):
                if os.path.isdir(os.path.join(root, name + ".app")):
                    return name
        return None
    if windows:
        for path in (
            os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ):
            if path and os.path.isfile(path):
                return path
        return None
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def app_window_command(url, browser, mac=None):
    """The command that opens `url` as an app window in `browser`."""
    mac = IS_MAC if mac is None else mac
    if not browser:
        return None
    if mac:
        # -n: a new instance, so the --app flag is honoured even if the browser
        # is already open.
        return ["open", "-na", browser, "--args", "--app=" + url, "--new-window"]
    return [browser, "--app=" + url, "--window-size=1180,860"]


def open_app_window(url=VIEWER_URL):
    command = app_window_command(url, find_app_browser())
    if command:
        try:
            subprocess.Popen(command, creationflags=no_window_flags(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            pass
    return webbrowser.open(url)


# ---------------------------------------------------------------------------
# Reminders that wait to be clicked away
# ---------------------------------------------------------------------------

def _applescript_string(text):
    return '"' + (text or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def sticky_notification_command(title, body, url=VIEWER_URL, mac=None):
    """A command that shows a reminder which stays until the student acts.

    macOS notifications fade after a few seconds, so a reminder is shown as an
    alert dialog instead: it stays on screen until "Dismiss" or "Open Mail
    Filter" is clicked, and the second opens the app.
    """
    mac = IS_MAC if mac is None else mac
    if mac:
        script = (
            "set choice to button returned of (display alert {title} message {body} "
            "as informational buttons {{\"Dismiss\", \"Open Mail Filter\"}} "
            "default button \"Open Mail Filter\")\n"
            "if choice is \"Open Mail Filter\" then open location {url}"
        ).format(title=_applescript_string(title), body=_applescript_string(body),
                 url=_applescript_string(url))
        return ["osascript", "-e", script]
    return ["notify-send", "--urgency=critical", "--app-name=" + APP_NAME, title, body]


def notify_sticky(title, body):
    """Show a reminder that waits to be clicked (macOS / Linux). True if shown."""
    command = sticky_notification_command(title, body)
    if not shutil.which(command[0]):
        return False
    try:
        # Detached: the dialog waits for the student, the reminder job does not.
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def start_ollama_command(mac=None):
    mac = IS_MAC if mac is None else mac
    if mac and os.path.isdir("/Applications/Ollama.app"):
        return ["open", "-g", "-a", "Ollama"]
    exe = shutil.which("ollama")
    if not exe and IS_WINDOWS:
        guess = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        exe = guess if os.path.isfile(guess) else None
    if not exe and mac and os.path.isfile("/usr/local/bin/ollama"):
        exe = "/usr/local/bin/ollama"
    return [exe, "serve"] if exe else None


# ---------------------------------------------------------------------------
# macOS LaunchAgents: the digest, the reminder, and opening at login
# ---------------------------------------------------------------------------

def launch_agents(install_dir):
    """label -> plist dict for the three jobs. Pure: builds, does not install.

    Paths are joined with "/" whatever system builds them: these are Mac paths.
    """
    import posixpath

    logs = posixpath.join(install_dir, "launchd.log")

    def job(label, script, extra):
        plist = {
            "Label": LAUNCH_AGENT_PREFIX + label,
            "ProgramArguments": ["/bin/bash", posixpath.join(install_dir, script)],
            "WorkingDirectory": install_dir,
            "StandardOutPath": logs,
            "StandardErrorPath": logs,
            "EnvironmentVariables": {
                "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONUTF8": "1",
            },
        }
        plist.update(extra)
        return plist

    return {
        LAUNCH_AGENT_PREFIX + "digest": job("digest", "run_digest.sh", {
            "StartCalendarInterval": {"Hour": DIGEST_TIME[0], "Minute": DIGEST_TIME[1]}}),
        LAUNCH_AGENT_PREFIX + "alert": job("alert", "run_alert.sh", {
            "StartCalendarInterval": {"Hour": ALERT_TIME[0], "Minute": ALERT_TIME[1]}}),
        LAUNCH_AGENT_PREFIX + "viewer": dict(job("viewer", "run_viewer.sh", {"RunAtLoad": True}),
                                             ProgramArguments=[
                                                 "/bin/bash", "-c",
                                                 "sleep {}; exec /bin/bash \"$0\" --quiet".format(
                                                     LOGIN_DELAY_SECONDS),
                                                 posixpath.join(install_dir, "run_viewer.sh")]),
    }


def launch_agents_dir():
    return os.path.expanduser("~/Library/LaunchAgents")


def install_launch_agents(install_dir):
    """Write and load the LaunchAgents. Returns a list of (label, ok)."""
    target = launch_agents_dir()
    os.makedirs(target, exist_ok=True)
    uid = str(os.getuid()) if hasattr(os, "getuid") else ""
    results = []
    for label, plist in launch_agents(install_dir).items():
        path = os.path.join(target, label + ".plist")
        with open(path, "wb") as fh:
            plistlib.dump(plist, fh)
        subprocess.run(["launchctl", "bootout", "gui/" + uid, path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        loaded = subprocess.run(["launchctl", "bootstrap", "gui/" + uid, path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if loaded.returncode != 0:  # older macOS
            loaded = subprocess.run(["launchctl", "load", "-w", path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        results.append((label, loaded.returncode == 0))
    return results


def uninstall_launch_agents():
    uid = str(os.getuid()) if hasattr(os, "getuid") else ""
    removed = []
    for label in (LAUNCH_AGENT_PREFIX + n for n in ("digest", "alert", "viewer")):
        path = os.path.join(launch_agents_dir(), label + ".plist")
        if os.path.exists(path):
            subprocess.run(["launchctl", "bootout", "gui/" + uid, path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.remove(path)
            removed.append(label)
    return removed


def main(argv):
    if len(argv) >= 2 and argv[1] == "install-launch-agents":
        directory = os.path.abspath(argv[2] if len(argv) > 2 else os.path.dirname(os.path.abspath(__file__)))
        for label, ok in install_launch_agents(directory):
            print("{} {}".format("loaded " if ok else "written (load failed)", label))
        return 0
    if len(argv) >= 2 and argv[1] == "uninstall-launch-agents":
        for label in uninstall_launch_agents():
            print("removed " + label)
        return 0
    if len(argv) >= 3 and argv[1] == "open-window":
        return 0 if open_app_window(argv[2]) else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
