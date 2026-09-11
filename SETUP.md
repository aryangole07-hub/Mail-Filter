# Setup — the one thing only you can do

**Everything here is free.** Classification runs on a local Ollama model on
your own machine (no API key, no per-run cost, nothing leaves the box), and
the Gmail API is free at this volume.

Everything else is already done and tested:

- `.venv` built on **Python 3.12.10**, all dependencies installed (`pip check` clean)
- `mail_filter.py` patched for six real bugs (see "What I changed" at the bottom)
- Classification wired to local **Ollama / `gemma3:4b`**, already on disk and verified
- Scheduled task **MailFilterDigest** registered and test-fired, next run **07:55 daily**
- Logs append to `digest.log` in this folder

Only Step B needs a human in a browser — about eight minutes.

---

## Step A — the classifier (already done, nothing to do)

Classification runs locally through **Ollama**, using the **`gemma3:4b`** model
you already have on disk. No account, no key, no spend, and no email text ever
leaves this machine.

Verified working on 2026-09-11: five sample campus emails classified correctly
in 3.3 seconds.

Nothing is required from you here. For reference:

- The server must be running. `run_digest.ps1` starts it automatically if the
  machine booted without it, so the 07:55 job is not at the mercy of whether
  Ollama Desktop autostarted.
- Check it yourself any time with `ollama ps` (running models) or
  `ollama list` (installed models).
- The model is loaded only for the few seconds a digest needs it, then
  explicitly unloaded, so it is not squatting in VRAM all day.
- To use a different local model: `$env:MAIL_FILTER_MODEL = 'gemma4:26b-a4b-it-qat'`
  (slower, sharper — also already on disk), or edit `CLASSIFY_MODEL` in
  `mail_filter.py`.

---

## Step B — Gmail credentials.json (8 minutes)

**The mailbox being read is `f20250420@hyderabad.bits-pilani.ac.in`.**

That's a Google Workspace account run by BITS, which adds one wrinkle: the
account that *owns the Cloud project* and the account whose *mail gets read*
do not have to be the same, and for a college account they often can't be.
Many university Workspace tenants block students from creating Cloud projects
or from granting third-party apps access.

So the plan is:

| Role | Account |
|---|---|
| Owns the Cloud project | whichever works — try the college account first, fall back to `aryangole07@gmail.com` |
| Added as a test user | **`f20250420@hyderabad.bits-pilani.ac.in`** |
| Signed in at the consent screen in Step C | **`f20250420@hyderabad.bits-pilani.ac.in`** |

Only the last two must be the college address. Getting the consent-screen
sign-in right is what actually decides which inbox you get.

Google has been renaming these screens, so I've given the current name with
the older one in brackets. If a menu item is missing, use the direct link.

### B1. Create a project

1. Go to **<https://console.cloud.google.com/>** and sign in as
   **`f20250420@hyderabad.bits-pilani.ac.in`**.

   If you land on an "administrator has disabled" message, or **New Project**
   is greyed out, or you're asked for a billing/organisation you don't have —
   BITS has restricted Cloud projects for students. That's normal and not a
   problem: sign out, sign back in with **`aryangole07@gmail.com`**, and build
   the project there instead. You'll still read the college inbox, because
   Step B4 and Step C are what select the mailbox.
2. At the top-left, click the **project dropdown** (next to the "Google Cloud"
   logo — it may say "Select a project").
3. Click **New Project** (top-right of the dialog).
4. Name: `Mail Filter`. Leave Location as **No organisation**. Click **Create**.
5. Wait for the notification bell, then make sure the project dropdown now
   shows **Mail Filter**. *If it still shows something else, click the
   dropdown and select Mail Filter.* Almost every "I can't find the button"
   problem below is actually the wrong project being selected.

### B2. Enable the Gmail API

1. Go to **<https://console.cloud.google.com/apis/library/gmail.googleapis.com>**
2. Confirm the project name at the top is **Mail Filter**.
3. Click the blue **Enable** button. Wait for it to finish.

### B3. Configure the consent screen

1. Go to **<https://console.cloud.google.com/auth/overview>**
   *(older console: APIs & Services → OAuth consent screen)*
2. If you see a **Get started** button, click it. Then fill in:
   - **App name**: `Mail Filter`
   - **User support email**: your own email (pick from the dropdown)
   - Click **Next**
   - **Audience**: choose **External** → **Next**
   - **Contact information**: your email again → **Next**
   - Tick **I agree to the Google API Services: User Data Policy**
   - Click **Create**

### B4. Add yourself as a test user

1. Go to **<https://console.cloud.google.com/auth/audience>**
   *(older console: OAuth consent screen → Test users)*
2. Under **Test users**, click **+ Add users**.
3. Type **`f20250420@hyderabad.bits-pilani.ac.in`**, press Enter, click
   **Save**.

   This is the step that points the app at your college inbox. If you built
   the project under your personal account, add `aryangole07@gmail.com` as a
   second test user too — harmless, and it lets you test either mailbox.

   Skipping this gives you `Error 403: access_denied` at sign-in.

### B5. Create the OAuth client

1. Go to **<https://console.cloud.google.com/auth/clients>**
   *(older console: APIs & Services → Credentials)*
2. Click **+ Create client** (or **Create Credentials → OAuth client ID**).
3. **Application type**: choose **Desktop app** from the dropdown.
   This matters — the script uses a loopback flow that only Desktop app
   clients allow. Don't pick "Web application".
4. **Name**: `Mail Filter Desktop`. Click **Create**.
5. A dialog appears. Click **Download JSON**.
6. Rename the downloaded file (it will have a long name like
   `client_secret_1234-abcd.apps.googleusercontent.com.json`) to exactly:

   ```
   credentials.json
   ```

7. Move it into **`E:\Projects\Mail Filter\`** — the same folder as
   `mail_filter.py`.

   Windows hides known extensions by default. If you see `credentials.json`
   but it still doesn't work, turn on **View → File name extensions** in
   Explorer and check it isn't really `credentials.json.json`.

### B6. Publish the app — don't skip this

While the app is in **Testing** mode, Google expires your saved login
**every 7 days**. Your 7:55am digest would silently stop working after a week,
and you'd have to re-authorise by hand each time.

1. Go to **<https://console.cloud.google.com/auth/audience>**
2. Under **Publishing status**, click **Publish app** → **Confirm**.

You do *not* need Google's verification review for this. Because the app
requests a sensitive scope while unverified, you'll see a
**"Google hasn't verified this app"** warning the first time you sign in —
that's expected for a personal app. Click **Advanced** →
**Go to Mail Filter (unsafe)**. It's your own project reading your own mail
with read-only access.

**If publishing is blocked, or sign-in later fails with an admin message:**
some Workspace tenants refuse unverified third-party apps outright. If that
happens, leave the app in **Testing** and keep the college address in the
test-user list. Everything still works — you'll just have to re-run
`mail_filter.py` by hand roughly once a week when Google expires the token.
The script is built for this: the scheduled run will fail with a readable
"run it manually once to sign in again" message in `digest.log` rather than
hanging or dying silently.

If BITS blocks the app even in Testing mode, the Gmail API route is closed
for that account and the fallback is an app password over IMAP, which would
need a different script. Tell me and I'll write it.

---

## Step C — first run (1 minute)

With both files in place:

```powershell
cd "E:\Projects\Mail Filter"
.\.venv\Scripts\python.exe mail_filter.py
```

A browser window opens. **Sign in as
`f20250420@hyderabad.bits-pilani.ac.in`** — this is the step that decides
which inbox gets summarised, so if the browser auto-selects your personal
account, click **Use another account** and switch. Then click through the
unverified-app warning if shown, and press **Continue / Allow** on the
read-only Gmail permission.

To confirm afterwards that it bound to the right mailbox:

```powershell
.\.venv\Scripts\python.exe check_account.py
```

That asks Gmail which account the saved token belongs to and prints it:

```
  Authorised mailbox : f20250420@hyderabad.bits-pilani.ac.in
  Messages in account: 4182

  Correct - this is the BITS college account.
```

If it names the wrong account, delete `token.json` and run
`mail_filter.py` again, choosing the college address this time.

**You must do this once manually.** It writes `token.json`, which every later
run reuses. The scheduled task deliberately refuses to open a browser (it
would hang forever on a window nobody can see), so it needs `token.json` to
already exist.

You should then see something like:

```
Checked 23 new email(s), 6 worth your attention.

📚 Classes (3)
----------------------------------------
  • Midsem timetable released
    from exams@univ.edu  |  Thu, 11 Sep 2026 09:15:00 +0530
...
(Filtered out 17 irrelevant email(s).)
```

If the first run says "No new emails since last run", widen the window:

```powershell
.\.venv\Scripts\python.exe mail_filter.py --hours 72
```

---

## Step D — confirm the schedule (30 seconds)

Already registered, but to watch it work with real credentials:

```powershell
Start-ScheduledTask -TaskName MailFilterDigest
Start-Sleep 20
Get-Content "E:\Projects\Mail Filter\digest.log" -Tail 30
```

Task details:

| | |
|---|---|
| Name | `MailFilterDigest` |
| Runs | Daily at **07:55** |
| Log | `E:\Projects\Mail Filter\digest.log` (rotates at 5 MB) |
| Missed runs | Run as soon as the PC is next on (`StartWhenAvailable`) |
| Requires | You to be logged in |

Change the time:

```powershell
Set-ScheduledTask -TaskName MailFilterDigest -Trigger (New-ScheduledTaskTrigger -Daily -At 8:30am)
```

Turn it off / on / remove it:

```powershell
Disable-ScheduledTask -TaskName MailFilterDigest
Enable-ScheduledTask  -TaskName MailFilterDigest
Unregister-ScheduledTask -TaskName MailFilterDigest -Confirm:$false
```

---

## Troubleshooting

| What you see | What it means |
|---|---|
| `could not reach Ollama at http://localhost:11434` | The local model server is not running. Start it with `ollama serve`, or install Ollama Desktop so it starts with Windows. |
| `Ollama is running but the model 'gemma3:4b' is not installed` | Pull it once: `ollama pull gemma3:4b`. |
| `Missing ...\credentials.json` | Step B5 — file missing, misnamed, or in the wrong folder. |
| `Error 403: access_denied` at sign-in | Step B4 — `f20250420@hyderabad.bits-pilani.ac.in` isn't in the test-user list. |
| `Access blocked: Mail Filter has not been verified` / "your administrator has not allowed" | BITS Workspace is blocking third-party apps. See the note at the end of Step B6. |
| Digest shows mail from the wrong inbox | You authorised the personal account. Delete `token.json`, run manually, and pick the college account. |
| **New Project** greyed out in Cloud Console | BITS restricts student Cloud projects. Build the project under `aryangole07@gmail.com` instead — Step B1. |
| `Google hasn't verified this app` | Expected. **Advanced → Go to Mail Filter (unsafe)**. |
| `Gmail authorisation is missing or expired, and this is a scheduled...` | `token.json` expired or was revoked. Run `mail_filter.py` manually once to re-authorise. If this happens weekly, you skipped **Step B6**. |
| `Access blocked: ... has not completed the Google verification process` | Publishing status is stuck. Re-check Step B6, or add yourself as a test user again. |
| `invalid_grant` | Clock skew or a revoked token. Delete `token.json` and run manually to sign in again. |
| Digest is empty every day | Normal if no new mail since the last run. State lives in `last_run.json` — delete it to reset. |
| Task shows `LastTaskResult: 1` | The script errored. Read `digest.log` — the reason is on the last line. |

**Full reset of Gmail auth:** delete `token.json`, then run
`.\.venv\Scripts\python.exe mail_filter.py` and sign in again.

---

## What I changed in `mail_filter.py`

The classification prompt and the digest layout were sound and are untouched.
Everything below is verified by `test_mail_filter.py` — 36 checks against a
fake Gmail and a fake classifier client. Run it any time:

```powershell
.\.venv\Scripts\python.exe test_mail_filter.py
```

### Bugs that would have shown up in daily use

1. **Encoded subject lines were never decoded.** The Gmail API returns
   headers exactly as they appear on the wire, so anything non-ASCII arrives
   RFC 2047-encoded. A real subject like `🎉 HackFest 2026 – registrations`
   reached the script as `=?UTF-8?B?8J+OiSBIYWNrRmVzdCAyMDI2...?=`. Campus
   mail trips this constantly — emoji, en-dashes, curly apostrophes, any
   non-English text. It hurt twice: the digest was unreadable, *and* the
   classifier was being handed base64 noise instead of the subject, so
   categories would have been wrong too. Now decoded properly.
2. **UTF-8 output.** The category emoji (📚 🎉 📌) crashed the script with
   `UnicodeEncodeError` whenever output was redirected to a file, because
   Windows defaults that stream to cp1252. This would have broken the 07:55
   job *every single day* while working fine by hand. Verified fixed through
   the real scheduled-task path.
3. **Snippets showed raw HTML entities** — `Register before 5 &amp; bring
   your ID &#39;card&#39;`. Now unescaped.
4. **A scheduled run with an expired token would hang forever** on a browser
   consent window nobody can see, leaving a stuck Python process. It now
   detects the non-interactive run and exits with an explanation.
5. **Revoked tokens crashed** with an uncaught `RefreshError` instead of
   re-authorising — exactly what Testing-mode's 7-day expiry triggers.
6. **`--max` above 500 silently truncated.** Gmail caps one page at 500 and
   the script never followed `nextPageToken`. Now paginates.
7. **Timezone edge in the Gmail query.** `after:` resolves in the account's
   local timezone, not UTC, so the window could start *later* than intended
   and silently skip mail. Now queries a day wider; the existing precise
   `internalDate` filter discards the surplus, so no duplicates.
8. **Messages with no headers** (some chat and draft records) raised
   `KeyError` and killed the whole run.

### Robustness

9. **A hallucinated message id from the model could overwrite a real email's
   category.** Returned ids are now checked against the batch that was
   actually sent, and any email the model skipped or mislabelled is backfilled
   as `Other` rather than quietly vanishing from the digest.
10. **One rate-limited API call used to cost you the entire digest.** Batches
    now fail independently — a bad one degrades to `Other` with a warning and
    the rest still classify. If *every* batch fails you get a clear error
    instead of a digest that misleadingly files everything under `Other`.
11. **Corrupt `token.json` or `last_run.json` crashed every subsequent run,
    permanently.** Both are now handled: warn, recover, carry on. Both files
    are also written atomically (temp file + rename), so an interrupted write
    can't create the corruption in the first place.

### Performance

12. **100 emails meant 100 sequential HTTPS round trips** — roughly half a
    minute of waiting. Gmail's batch endpoint now collapses each group of 50
    into one request. If batching is unavailable or errors, it falls back to
    the original serial path, so the worst case is today's behaviour.

### Housekeeping

13. Added `--no-save` (see a digest without advancing the last-run marker —
    useful for testing), rejected nonsense `--max`/`--hours` values up front,
    dropped an unused `base64` import, and set `cache_discovery=False` to keep
    a noisy library warning out of `digest.log`.

### Guarding against a wrong or manipulated classification

Email content is written by strangers and the model can simply be wrong, so
nothing in the classification path trusts the model's reply. Eight layers, in
order of how much work they do:

1. **The API enforces a JSON schema.** `category` is an `enum` of the four real
   categories, so an invalid label is now *impossible* rather than merely
   caught. `index` must be an integer, and `additionalProperties: false`
   forbids anything else. The schema is identical on every call, so it stays
   in the API's schema cache instead of recompiling each run.
2. **Emails are referenced by a small index, never by their Gmail id.** A
   16-character hex id is easy to garble or invent convincingly; `[3]` is not.
   The ids never enter the prompt at all, so a hallucinated one cannot collide
   with a real message.
3. **Every returned row is re-validated locally anyway** — index in range, an
   integer and not a bool (`True` would otherwise read as index 1), no
   duplicates (first answer wins), category in the enum. Anything failing a
   check is dropped rather than trusted.
4. **Unresolved emails are retried, asking only about the stragglers** — twice,
   before giving up. This covers a truncated reply (`stop_reason:
   "max_tokens"`), a refusal, a transient 429, and a model that simply skipped
   a row.
5. **Whatever is still unresolved is shown, not hidden.** The fallback is
   `Other` — a visible category — and never `Ignore`. This is the rule the
   whole path is built around: a stray newsletter in the digest is a nuisance,
   a silently swallowed exam notice is a real problem.
6. **Untrusted email text is fenced and capped.** Subjects, senders and
   previews are length-capped and flattened to a single line, so a crafted mail
   can't pad the prompt or forge a fake `[n]` entry to impersonate another
   email. The prompt states the block is data rather than instructions, and
   tells the model that an email trying to give it orders is itself evidence of
   spam.
7. **A keyword net that overrules the model.** If a mail was marked `Ignore`
   but reads as plainly academic (midsem, compre, exit test, quiz, viva,
   grade sheet, re-evaluation, deadlines, …), it is put back in front of you
   and you're told why. It only ever moves mail *out* of `Ignore`, so a false
   positive costs one extra line and nothing more.
8. **Run-level anomaly detection.** A run where every single email came back
   `Ignore` is flagged — that can be a quiet morning, but it's also what a
   failed or manipulated classification looks like. `--debug` prints the raw
   reply for each batch so you can see exactly what the model said.

Reaching the API but getting an unusable answer is treated differently from
not reaching it at all: the first falls back to showing the mail under `Other`,
while the second exits without writing `last_run.json`, so the next run retries
those emails instead of a placeholder digest standing in for them.

`temperature` is pinned to 0, so the same inbox classifies the same way twice —
something the hosted SDK no longer allowed. The schema is still the stronger
guarantee of the two: it makes a category outside the four real ones
structurally impossible rather than merely unlikely.

### Deliberately left alone

`save_last_run` still runs only after a successful digest, so a crash
anywhere above it leaves the window intact and the next run re-checks the
same mail rather than losing it. That is the right trade for a digest: a
repeated email is a minor annoyance, a missed one is a real problem.
