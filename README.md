# Mail Filter

Reads your recent Gmail and sorts it into:

- **Classes** — midsems, compres, exit tests, quizzes, assignments, class participation, cancelled lectures/tutorials
- **Fests** — hackathons, events, fests, competitions, workshops
- **Other** — replies to your mails, registration/portal announcements, hostel notices, policy changes
- *(Ignore)* — promos, newsletters, spam — filtered out silently

This runs **entirely on your computer** — Gmail is read over the free API, and
classification runs on a local Ollama model. **No API key and no running cost.**

> **Set up on this machine already.** Virtualenv, dependencies, the local
> classifier and the daily scheduled task are done. Only the Google Cloud
> browser steps are left — see **[SETUP.md](SETUP.md)** for exact
> click-by-click instructions and troubleshooting.
>
> Target mailbox: **`f20250420@hyderabad.bits-pilani.ac.in`** (BITS Hyderabad).
> Run `.\.venv\Scripts\python.exe check_account.py` after the first sign-in
> to confirm it authorised that account and not a personal one.

## 1. Get Gmail API access

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and create a project (any name).
2. In **APIs & Services → Library**, search for **Gmail API** and enable it.
3. Go to **APIs & Services → OAuth consent screen**. Choose **External**, fill in the required fields (app name, your email), and add yourself as a **test user**.
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**. Choose **Desktop app** as the type.
5. Download the resulting JSON and save it as `credentials.json` in this same folder as `mail_filter.py`.

## 2. The classifier (local, nothing to set up)

Classification runs on [Ollama](https://ollama.com/) with the **`gemma3:4b`**
model — already installed here. No account, no API key, no per-run cost, and
no email text leaves the machine.

```powershell
ollama list          # confirm gemma3:4b is installed
ollama pull gemma3:4b   # only if it isn't
```

To use a different local model, set `MAIL_FILTER_MODEL` or edit
`CLASSIFY_MODEL` in `mail_filter.py`. Point it at a non-default server with
`OLLAMA_HOST`.

## 3. Install dependencies

Already done here, in a Python 3.12 virtualenv at `.venv`. To rebuild it:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 4. Run it

```powershell
.\.venv\Scripts\python.exe mail_filter.py
```

The first run opens a browser window asking you to log into Google and approve read-only access to Gmail. After that, it saves a `token.json` so you won't need to log in again.

By default, the first run looks back 24 hours. Every run after that only checks mail received since the *previous* run (tracked in `last_run.json`), so you get a clean incremental digest each time — no duplicates.

Options:
```bash
python mail_filter.py --hours 48   # first-run lookback window
python mail_filter.py --max 200    # max emails fetched per run
```

## 5. (Optional) Run it automatically

**This machine (Windows)** — already configured. A Task Scheduler job named
`MailFilterDigest` runs `run_digest.ps1` daily at **07:55** and appends to
`digest.log`. See [SETUP.md](SETUP.md#step-d--confirm-the-schedule-30-seconds)
to change the time, disable it, or read the log.

Don't schedule `python mail_filter.py` directly — use `run_digest.ps1`. It
forces UTF-8 (otherwise the emoji below crash the run when output goes to a
log file), starts the Ollama server if the machine booted without it, marks
the run non-interactive so a stale token can't hang the task on an invisible
browser window, and rotates the log.

**macOS/Linux (cron)** — for reference:
```bash
crontab -e
# add this line (adjust paths):
55 7 * * * cd /path/to/mail_filter && PYTHONUTF8=1 MAIL_FILTER_NONINTERACTIVE=1 ./.venv/bin/python mail_filter.py >> digest.log 2>&1
```

## 6. Opening at startup, and reminders that wait for you

```powershell
powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
```

Run once, no admin needed. It sets up four things, all for the current user:

- **`MailFilterViewer`** — a Task Scheduler job that fires 30 seconds after
  you sign in and runs `run_viewer.ps1`, so your mail is already open and
  waiting when you get to the desk. If the window is somehow open already it
  is focused rather than duplicated.
- **The notification identity** — reminders are labelled *Mail Filter* and get
  their own entry in Settings › System › Notifications. Windows refuses to
  show a toast from an unregistered app, so this is required, not decoration.
- **The `mailfilter:` protocol** — what the **Open Mail Filter** button on a
  reminder activates.
- **A Start-menu shortcut**, for opening it by hand.

Reminders are sent by `notify.ps1` using Windows' **reminder** toast scenario:
the notification stays on screen until you click it away, instead of fading
after five seconds and taking the reminder with it. Two things keep that
working, so don't remove either — a reminder toast must carry at least one
button (Windows quietly downgrades a button-less one to an ordinary fading
toast), and it must be sent under a registered AppUserModelID. Both are
covered by tests. On a machine where the toast platform is unavailable it
falls back to an old tray balloon, which does fade; `alerts.py` prints a line
saying so rather than pretending the reminder was sticky.

Undo all of it with:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall
```

That leaves the digest and reminder tasks alone.

## The viewer

```powershell
.\run_viewer.ps1
```

Opens Mail Filter as its own desktop window - no address bar, its own
taskbar button - and starts the local server behind it if it is not
already running. (`.\view.ps1` does the same in a console plus a browser
tab, if you prefer that.)

Opens a local website at `http://127.0.0.1:8765/` showing every mail worth
your attention: the subject as a heading, an AI summary underneath, and two
buttons.

- **Original** opens the message exactly as it was sent, unedited. It is
  displayed inside a sandboxed frame that permits no scripts and no network
  access, and remote images stay blocked until you click **Load images** -
  most remote images in email are tracking pixels that tell the sender when
  you opened it.
- **Mark unimportant** pushes a mail down into Filtered. That feeds back into
  the classifier: those senders are passed to the model as guidance on the
  next run, and they are the one thing allowed to override the never-hide
  rules below - including the college-domain rule, so it is how you get rid of
  a college mailing list you genuinely do not want. Every one can be undone.

Each card carries exactly one of those two actions, whichever fits where the
card is: mail being shown gets **Mark unimportant**, mail in Filtered gets
**Mark important**, and a mail you have already pinned gets **Unmark
important**. Mail that can never be hidden (anything about marks or grades)
shows the button disabled rather than hiding it, so the rule is visible
instead of mysterious.

There is also a **Filtered** tab. Mail the classifier hid is still listed
there, so nothing is ever actually invisible - a wrong Ignore costs you one
click, not a missed email.

The **⟳ Refresh** button checks Gmail for real: it runs a full digest (fetch,
classify, extract dates) and then reloads the page, so you are not stuck
looking at whatever the 07:55 task last found. It takes anywhere from a few
seconds to a couple of minutes depending on how much new mail there is - the
button spins until it is done, then a toast says how many arrived, or what
went wrong. On Windows it goes through `run_digest.ps1`, so an on-demand check
starts Ollama if it is not running and is recorded in `digest.log` like any
other run.

Only one digest runs at a time. A `run.lock` file keeps a Refresh and the
07:55 task from fetching and classifying the same mail twice over each other;
whichever is second exits with code 75 and says so instead of racing.

## Ask - a chat with your own inbox

The **Ask** tab is a conversation with `gemma3:4b` running locally on this
machine. It remembers the last few turns, so follow-ups work: ask *"when is my
next quiz?"*, then *"who sent that?"*, and the second question is answered
from the mail the first one was about rather than from whatever else happens
to contain the word "sent".

What it will not do is make something up:

- the model only ever sees mail from your own store, handed to it as numbered
  blocks - it has no other source;
- the schema makes it cite the numbers it used, and **an answer that claims to
  have read your mail but cites nothing is thrown away**, not shown;
- every citation is mapped back to a real message and listed under the reply,
  so the original is one click away.

It is allowed to be sociable - a greeting gets a greeting - but a turn where
no mail matched is told, in that turn, that it may state nothing as fact, and
the reply is labelled "Nothing here was traced to an email" in the page. That
label is the honest one to read: prose without sources under it is chat, not
evidence.

The conversation lives in the page (and in `sessionStorage`), never on disk -
**New chat** clears it. Nothing about any of this leaves the machine: the mail
and the model are both local.

The server binds to `127.0.0.1`, so the site is reachable only from this
computer. Your mail is never uploaded anywhere.

## How much to trust the classification

Emails are written by strangers, and the model can be wrong, so the classifier
is wrapped in several layers of checking: a JSON schema constrains decoding so an
invalid category can't be produced, emails are referred to by index rather than
by Gmail id, every row is re-validated locally, unresolved ones are retried,
and anything still unresolved is shown under **Other** rather than hidden.

**Mail is never hidden just because the model said so.** An `Ignore` is
overruled whenever any of these is true:

1. the sender is a university address (any `bits-pilani.ac.in` domain)
2. the subject or preview mentions anything with a consequence - exams,
   deadlines, fees, forms, results, placements, hostel matters, and so on
3. it is a reply to a thread you started

Only a mail you have explicitly **Reported** can bypass those rules. And even
a hidden mail is still listed under the viewer's **Filtered** tab.

This is deliberately lopsided. It lets some junk through, because a stray
newsletter costs you one line to skim and a missed exam notice does not.

Untrusted email text is capped and fenced off in the prompt, so a mail can't
forge an entry or issue instructions. A keyword net overrules the model
outright if something plainly academic was marked Ignore, and a run where
*everything* came back Ignore is flagged as suspicious.

The rule throughout: when in doubt, show the email. Run with `--debug` to see
the raw classification replies.

## Tests

`test_mail_filter.py` exercises the fetch, decode, classify and digest paths
against a fake Gmail and a fake classifier client — no network, no
credentials, no model calls:

```powershell
.\.venv\Scripts\python.exe test_mail_filter.py
```

## Notes

- Only read access to Gmail is requested — the script never sends, deletes, or modifies anything.
- `credentials.json` and `token.json` are secrets, and `digest_store.json` and `feedback.json` contain your actual mail. All four are in `.gitignore` — never commit or share them.
- Classification is free: it runs locally on `gemma3:4b`. The model is loaded for the few seconds a digest needs and then unloaded, so it doesn't sit in VRAM all day.
- A digest of 50–100 emails takes a few seconds. The first run after a reboot is slower because the model has to load.
- If you want different categories or rules, edit `CATEGORY_DESCRIPTIONS` near the top of `mail_filter.py` — the wording there is literally what's sent to the model, so plain-English changes are enough.
