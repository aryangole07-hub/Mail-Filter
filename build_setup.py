#!/usr/bin/env python3
"""
build_setup.py - turn setup_wizard.py into dist/setup.exe.

Run from the project folder:

    .\\.venv\\Scripts\\python.exe build_setup.py

The result at dist/setup.exe carries its own Python, the application source,
and your Google OAuth client, so a friend needs nothing installed first.

IMPORTANT: the built exe embeds credentials.json. That is deliberate - it is
what makes the install one click for the people you share it with - but it
means the exe should be handed over directly (a chat, a drive link), NOT
committed to the public repository. dist/ is gitignored for that reason.
Google treats desktop-app client secrets as non-confidential, and the project
is capped at 100 users, so the blast radius is small; but "small" is not
"none", so keep it off the internet.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Everything the installed app needs at runtime.
PAYLOAD = [
    "mail_filter.py", "viewer.py", "courses.py", "events.py", "qa.py",
    "alerts.py", "run_alert.ps1", "notify.ps1",
    "run_viewer.ps1", "install_autostart.ps1",
    "ui.html", "check_account.py", "run_digest.ps1", "view.ps1",
    "requirements.txt", "README.md",
]


def fail(message):
    print("\n" + message)
    return 1


def main():
    missing = [f for f in PAYLOAD if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        return fail("Missing application files: " + ", ".join(missing))

    creds = os.path.join(HERE, "credentials.json")
    bundle_creds = os.path.exists(creds)
    if not bundle_creds:
        print("WARNING: credentials.json not found.")
        print("The exe will build, but whoever runs it will be asked to supply")
        print("their own credentials.json instead of getting a one-click setup.")
        if input("Continue anyway? [y/N] ").strip().lower() != "y":
            return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Installing it now...")
        out = subprocess.run([sys.executable, "-m", "pip", "install",
                              "--quiet", "pyinstaller"])
        if out.returncode != 0:
            return fail("Could not install PyInstaller.")

    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",             # no console window behind the wizard
        "--name", "setup",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", os.path.join(HERE, "build"),
    ]
    for name in PAYLOAD:
        cmd += ["--add-data", os.path.join(HERE, name) + sep + "."]
    if bundle_creds:
        cmd += ["--add-data", creds + sep + "."]
    icon = os.path.join(HERE, "setup.ico")
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(HERE, "setup_wizard.py"))

    print("Building setup.exe ...")
    out = subprocess.run(cmd)
    if out.returncode != 0:
        return fail("PyInstaller failed. See the output above.")

    exe = os.path.join(HERE, "dist", "setup.exe")
    if not os.path.exists(exe):
        return fail("PyInstaller reported success but produced no exe.")

    size_mb = os.path.getsize(exe) / (1024 * 1024)
    print("\nBuilt: {}  ({:.1f} MB)".format(exe, size_mb))
    if bundle_creds:
        print("It embeds credentials.json - share the file directly, and do")
        print("not commit it or upload it anywhere public.")
    else:
        print("It does NOT embed credentials.json - recipients will be asked")
        print("to supply their own.")

    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
