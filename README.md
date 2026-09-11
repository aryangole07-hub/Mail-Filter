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

## How much to trust the classification

Emails are written by strangers, and the model can be wrong, so the classifier
is wrapped in several layers of checking: a JSON schema constrains decoding so an
invalid category can't be produced, emails are referred to by index rather than
by Gmail id, every row is re-validated locally, unresolved ones are retried,
and anything still unresolved is shown under **Other** rather than hidden.

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
- `credentials.json` and `token.json` are secrets. They're in `.gitignore` — never commit or share them.
- Classification is free: it runs locally on `gemma3:4b`. The model is loaded for the few seconds a digest needs and then unloaded, so it doesn't sit in VRAM all day.
- A digest of 50–100 emails takes a few seconds. The first run after a reboot is slower because the model has to load.
- If you want different categories or rules, edit `CATEGORY_DESCRIPTIONS` near the top of `mail_filter.py` — the wording there is literally what's sent to the model, so plain-English changes are enough.
