#!/usr/bin/env python3
r"""
check_account.py - prints which mailbox mail_filter.py is actually authorised
against, so you can confirm it bound to the college account and not a
personal one.

    .\.venv\Scripts\python.exe check_account.py
"""
import os
import sys

from mail_filter import TOKEN_FILE, get_gmail_service

if not os.path.exists(TOKEN_FILE):
    sys.exit(
        "No token.json yet - run 'python mail_filter.py' once and sign in first."
    )

profile = get_gmail_service().users().getProfile(userId="me").execute()

print()
print(f"  Authorised mailbox : {profile['emailAddress']}")
print(f"  Messages in account: {profile.get('messagesTotal', '?')}")
print()

if profile["emailAddress"].lower().endswith("hyderabad.bits-pilani.ac.in"):
    print("  Correct - this is the BITS college account.")
else:
    print("  NOT the college account. Delete token.json and run")
    print("  mail_filter.py again, choosing the f20250420@... address.")
