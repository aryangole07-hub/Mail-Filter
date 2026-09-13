#!/usr/bin/env python3
"""
build_setup.py - build what friends install.

    .\\.venv\\Scripts\\python.exe build_setup.py          # both
    .\\.venv\\Scripts\\python.exe build_setup.py --exe    # Windows setup.exe only
    .\\.venv\\Scripts\\python.exe build_setup.py --mac    # MailFilter-mac.zip only

dist/setup.exe       Windows. Carries its own Python, the app, and your Google
                     OAuth client; setup_wizard.py compiled by PyInstaller.
dist/MailFilter-mac.zip
                     macOS. A folder with the app, the OAuth client, the
                     setup wizard and "Install Mail Filter.command", which a
                     Mac user right-click-opens. A Mac cannot run a Windows exe
                     and PyInstaller cannot cross-build a Mac app from Windows,
                     so the Mac installer runs the same wizard on the Mac's own
                     Python. Shell scripts are stored as executable.

IMPORTANT: both embed credentials.json. That is deliberate - it is what makes
the install one click for the people you share it with - but it means they
must be handed over directly (a chat, a drive link), NOT committed to the
public repository. dist/ is gitignored for that reason. Google treats
desktop-app client secrets as non-confidential, and the project is capped at
100 users, so the blast radius is small; but "small" is not "none", so keep it
off the internet.
"""

import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Everything the installed app needs at runtime, on any system.
PAYLOAD = [
    "mail_filter.py", "viewer.py", "courses.py", "events.py", "qa.py",
    "alerts.py", "people.py", "profile.py", "attachments.py", "user_notes.py",
    "calendar_store.py", "conflicts.py", "todo_store.py", "marks.py",
    "timetable_import.py", "platforms.py",
    "ui.html", "check_account.py", "requirements.txt", "README.md",
    "SETUP-FOR-FRIENDS.md",
    # Windows launchers
    "run_alert.ps1", "notify.ps1", "run_viewer.ps1", "install_autostart.ps1",
    "run_digest.ps1", "view.ps1",
    # macOS / Linux launchers
    "run_digest.sh", "run_viewer.sh", "run_alert.sh", "install_autostart_mac.sh",
]
MAC_EXTRA = ["setup_wizard.py", "Install Mail Filter.command"]


def fail(message):
    print("\n" + message)
    return 1


def check_payload():
    """Every file the wizard installs must be shipped, and must exist."""
    import setup_wizard

    wanted = set(setup_wizard.APP_FILES + setup_wizard.WINDOWS_FILES + setup_wizard.UNIX_FILES)
    unlisted = sorted(wanted - set(PAYLOAD))
    if unlisted:
        return "The wizard installs files this build does not ship: " + ", ".join(unlisted)
    missing = [f for f in PAYLOAD + MAC_EXTRA if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        return "Missing application files: " + ", ".join(missing)
    return None


def credentials_path():
    creds = os.path.join(HERE, "credentials.json")
    if os.path.exists(creds):
        return creds
    print("WARNING: credentials.json not found.")
    print("The build will work, but whoever runs it will be asked to supply")
    print("their own credentials.json instead of getting a one-click setup.")
    if input("Continue anyway? [y/N] ").strip().lower() != "y":
        return False
    return None


def build_exe(creds):
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Installing it now...")
        if subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pyinstaller"]).returncode:
            return fail("Could not install PyInstaller.")

    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--windowed", "--name", "setup",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", os.path.join(HERE, "build"),
    ]
    for name in PAYLOAD:
        cmd += ["--add-data", os.path.join(HERE, name) + sep + "."]
    if creds:
        cmd += ["--add-data", creds + sep + "."]
    icon = os.path.join(HERE, "setup.ico")
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(HERE, "setup_wizard.py"))

    print("Building setup.exe ...")
    if subprocess.run(cmd).returncode != 0:
        return fail("PyInstaller failed. See the output above.")
    exe = os.path.join(HERE, "dist", "setup.exe")
    if not os.path.exists(exe):
        return fail("PyInstaller reported success but produced no exe.")
    print("Built: {}  ({:.1f} MB)".format(exe, os.path.getsize(exe) / (1024 * 1024)))
    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    return 0


def build_mac_zip(creds, target=None):
    """dist/MailFilter-mac.zip with executable scripts and Unix line endings."""
    target = target or os.path.join(HERE, "dist", "MailFilter-mac.zip")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    folder = "Mail Filter Setup/"
    files = PAYLOAD + MAC_EXTRA + (["credentials.json"] if creds else [])
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            source = creds if name == "credentials.json" else os.path.join(HERE, name)
            with open(source, "rb") as fh:
                data = fh.read()
            executable = name.endswith((".sh", ".command"))
            if executable:
                data = data.replace(b"\r\n", b"\n")  # CRLF breaks bash on a Mac
            info = zipfile.ZipInfo(folder + name)
            info.create_system = 3  # Unix, so the permission bits below are honoured
            info.external_attr = ((0o755 if executable else 0o644) | 0o100000) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        guide = os.path.join(HERE, "SETUP-FOR-FRIENDS.md")
        info = zipfile.ZipInfo(folder + "HOW TO INSTALL.txt")
        info.create_system = 3
        info.external_attr = (0o644 | 0o100000) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(guide, "rb") as fh:
            archive.writestr(info, fh.read())
    print("Built: {}  ({:.1f} MB)".format(target, os.path.getsize(target) / (1024 * 1024)))
    return target


def main(argv):
    want_exe = "--mac" not in argv
    want_mac = "--exe" not in argv
    problem = check_payload()
    if problem:
        return fail(problem)
    creds = credentials_path()
    if creds is False:
        return 1
    if want_mac:
        build_mac_zip(creds)
    if want_exe:
        if os.name != "nt":
            print("Skipping setup.exe: build it on Windows.")
        elif build_exe(creds):
            return 1
    if creds:
        print("\nBoth builds embed credentials.json - share them directly, and do")
        print("not commit them or upload them anywhere public.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
