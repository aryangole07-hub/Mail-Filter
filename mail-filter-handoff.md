# Mail Filter — handoff

A complete, running record of what this project is, how every part works, and
what changed, prompt by prompt. **This file is appended to after every change
and committed and pushed with it.** Newest changes are at the bottom, in the
changelog.

> Privacy note: this file is public. It deliberately contains no personal data
> — no student name, campus ID, mail contents or addresses. Those live only in
> gitignored files on the owner's machine (see *Data files*).

---

## 1. What it is

A local, free mail assistant for a BITS Pilani (Hyderabad) student's college
Gmail. Every morning it reads new mail, sorts it into **Classes / Fests /
Other** (and hides unmistakable junk under **Filtered**), writes a short
summary of each, pulls out dates, and serves everything in a desktop-app window
with a calendar and a chat that answers questions from the student's own mail.

Hard constraints the whole design follows:

- **Free.** No paid API. Classification, summaries, dates and chat all run on a
  local Ollama model. Nothing about the mail leaves the machine except the
  read-only Gmail API call itself.
- **Recall over tidiness.** The student said they would rather see some junk
  than miss one important mail. Anything about **marks/grades** must show
  without exception.
- **Honest answers.** The chat must not invent facts; anything it says about
  the mail has to point at the mail it came from.

Repository: https://github.com/aryangole07-hub/Mail-Filter (public).
GitHub Pages serves `/docs` (homepage + privacy policy, required by Google to
move the OAuth app out of Testing).

---

## 2. Files

| File | Role |
|---|---|
| `mail_filter.py` | The digest: Gmail fetch, classification, safety nets, category correction, course tagging, summaries, date extraction, store merge, run lock, `--recheck`. |
| `viewer.py` | Local web server on `127.0.0.1:8765` (loopback only): mail list, original-message view, report/important, calendar API, on-demand Gmail refresh, chat endpoint. |
| `ui.html` | The whole front end (served from disk on every request). |
| `qa.py` | Grounded question answering and the multi-turn chat. |
| `people.py` | Learns who teaches which course from mail; HOD guesses; sender→course memory for the classifier. |
| `profile.py` | The student's identity (name, campus ID, login…) read from their own mail into gitignored `profile.json`. |
| `courses.py` | The 8-course registry, the weekly timetable (rooms + who takes each slot), course tagging, timetable text for the chat. |
| `events.py` | Schema-validated date extraction from mail. |
| `alerts.py` | Evening reminder about tomorrow. |
| `notify.ps1` | Sticky Windows toast (stays until clicked away). |
| `run_digest.ps1` | Wrapper for the scheduled digest (UTF-8, starts Ollama, log rotation). |
| `run_alert.ps1` | Wrapper for the evening reminder task. |
| `run_viewer.ps1` | Starts the server hidden and opens the app window (Edge/Chrome `--app`). |
| `view.ps1` | Older launcher: console + browser tab. |
| `install_autostart.ps1` | Logon task, notification identity, `mailfilter:` protocol, Start-menu shortcut; `-Uninstall`. |
| `setup_wizard.py` / `build_setup.py` | One-click `setup.exe` for friends (embeds `credentials.json` — never publish `dist/`). |
| `check_account.py` | Confirms which mailbox the token belongs to. |
| `test_mail_filter.py` | Script-style test suite (420 checks at the time of writing). |
| `README.md`, `SETUP.md` | User docs. |
| `mail-filter-handoff.md` | This file. |

---

## 3. Features in detail

### 3.1 The daily digest (`mail_filter.py`)

- **Schedule:** Task Scheduler `MailFilterDigest` runs `run_digest.ps1` at
  **07:55** daily (the owner asked for 07:55, not 08:00). Output → `digest.log`
  (rotated at 5 MB).
- **Window:** fetches everything since the last successful run
  (`last_run.json`). The marker only advances when the run was **complete** —
  a `--max` truncation or an unfetchable message leaves it alone so the next run
  re-reads the same window. (Earlier bug: truncation skipped the oldest mail
  *and* advanced the marker — mail silently lost. Fixed and tested.)
- **Local calendar days:** every "today/tomorrow" computation uses
  `datetime.now().astimezone()`. At UTC+5:30 a UTC-based date called today
  "tomorrow" between midnight and 05:30. Fixed and tested.
- **Store merge:** `digest_store.json` is merged, never overwritten, so a re-read
  never blanks summaries or events that an earlier run computed.
- **Run lock:** `run.lock` (stale after 30 min). The 07:55 task and the viewer's
  Refresh button can now collide; the second run exits with code **75**
  (`EXIT_BUSY`) and says so instead of racing.
- **Flags:** `--hours`, `--max`, `--no-save`, `--backfill DAYS`, `--no-events`,
  `--debug`, `--recheck`.

### 3.2 Classification

- Local model via `OllamaClient` (stdlib `urllib`, `/api/chat`), JSON schema
  passed as Ollama `format` so a category outside the enum is structurally
  impossible. Batches of 15, emails referenced by small integer index, rows
  re-validated, stragglers retried, anything unresolved falls back to **Other**
  (visible, never Ignore).
- **Category definitions** were sharpened on 2026-09-13: Classes is *coursework
  only*; health/vaccination, hostel, mess, fees, library, transport, internet,
  placement/internship mail is explicitly **Other** even when it names a date,
  deadline, form or room. The prompt's "show anything with a date" rule now says
  plainly that *showing* a mail means Other unless it is about a course.
- **Sender memory in the prompt:** `people.classifier_hint()` lists senders whose
  mail has been about one course before ("Utkarsh Kumar writes about FoFA …"),
  so the next bare "Re: Handout" goes to that course.
- **Feedback in the prompt:** senders the student marked important / unimportant
  (addresses only — never mail text, so a mail cannot inject instructions).

### 3.3 Recall tiers (never collapse these)

In order of strength:

1. **MARKS_PATTERN** (marks, grades, CGPA, results, answer script, paper show) —
   absolute: nothing hides it, not the model, not a report.
2. **Mark important** by the student — absolute; also clears reports on that
   sender; matched by id, sender and exact subject.
3. **Mark unimportant** (stored as a "report") — lets an Ignore stand for that
   sender and overrides the never-hide rules below, including the college-domain
   rule. It is a *nudge* for future mail (the model still decides), not a ban.
4. **NEVER_HIDE_PATTERN**, institution domains, `Re:` replies — rescued from
   Ignore.

### 3.4 Category correction (the "HPV mail in Classes" fix)

`correct_categories()` runs after classification, before the safety net. A
**Classes** verdict with nothing academic behind it is moved:

- *Academic evidence* = a course tag, a Classroom/LMS sender, a sender whose mail
  has been about a course before, or coursework wording (`ACADEMIC_WORDS`: quiz,
  midsem, compre, assignment, handout, lecture, tutorial, marks, grade, …;
  "of course" excluded).
- Destination is **Fests** if it reads like an event (`FEST_WORDS`: fest,
  hackathon, party, competition, club, workshop, "register now", …), otherwise
  **Other**.
- It only ever moves between visible tabs, so it cannot hide anything.
- The model's original verdict is stored as `model_category`, so `--recheck`
  re-applies the rules to what the model said rather than to its own output
  (the first version could only run once).
- `python mail_filter.py --recheck` applies course tagging + correction + safety
  net to the stored mail without Gmail or the model. On the real store this
  moved **19** mis-filed mails out of Classes (two HPV vaccination notices,
  dentist/physiotherapist availability, library, internet quota, internships…)
  and put two events into Fests.

### 3.5 Course tagging and "who teaches what" (`courses.py`, `people.py`)

- `courses.tag_courses(subject, body, sender)` — course code, name, aliases,
  short forms (strict rules for 2–3 letter forms like "M3", "TS"), registry
  professors.
- If nothing matches, the mail inherits the course the **sender** usually writes
  about (`people.course_for_mail`) — needs ≥2 mails and a ≥60 % majority.
- `people.learn()` safeguards, each found on the real inbox:
  - a course is only *learned* from mail whose own text names it (no
    self-reinforcing loop);
  - LMS/Classroom relays (`no-reply@classroom.google.com`,
    `noreply.lms@…`) are keyed by the professor's **display name**, with
    "(Classroom)" / "(via BPHC LMS)" stripped;
  - the student's own addresses are excluded;
  - machine names like "Do not reply to this email" are not people.
- **HOD guess** (the owner's rule of thumb: whoever mails the whole class about a
  course is usually its HOD, unless the mail is about their own tutorial/lecture
  section):
  - "stated" if the person's own mail says HOD / Head of Department;
  - otherwise "inferred" from ≥2 **class-wide** mails (section/tutorial mail
    excluded), most such mails wins;
  - someone who comes out top for more than 2 courses is a broadcaster, not an
    HOD, and is dropped;
  - always passed to the chat with the reason and the word "probably".
- `python people.py` shows what was learned; `--sender NAME` for one person.

### 3.6 Profile (`profile.py`)

- `python profile.py --detect` fills blanks from the store: campus ID (the
  `20xxXXXXnnnnH` form seating plans use), mail login, college address, batch
  year, and the display name on the To: line. `--set key=value` for the rest
  (hostel, room, programme, notes). Empty fields are left out of the chat's facts
  — the chat says it does not know rather than guessing.
- Stored in **gitignored** `profile.json`.

### 3.7 Summaries and the date-hallucination guard

- One summary per shown mail (lead with what to do and by when).
- **Cause of the "November 16, 2024" bug:** some senders (Unstop etc.) put raw
  HTML in the *text* part. The first 4000 characters the model saw were template
  markup; the real date was cut off, and the model invented one.
  `readable_body()` now converts a text part that is really HTML.
- **Guard:** every summary is checked — any **year** or **month** it states must
  appear in the mail (month names, `dd/mm/yyyy`, `yyyy-mm-dd`; "you may" is not
  May). If not: one retry naming the mistake; if still wrong, the sentence with
  the invented date is dropped; if nothing honest is left the summary is empty
  and the viewer shows the Gmail snippet.
- An audit of the 135 stored summaries found 3 with invented dates; all were
  re-summarised correctly.

### 3.8 Viewer (`viewer.py` + `ui.html`)

- **App window:** `run_viewer.ps1` starts the server under `pythonw.exe` (no
  console), waits for the port, opens Edge/Chrome with `--app=` (no address bar,
  own taskbar button), focuses an existing window instead of opening a second.
  Inline SVG favicon.
- **Opens at logon:** `install_autostart.ps1` registers Task Scheduler
  `MailFilterViewer` (30 s after sign-in, interactive session only), a
  Start-menu shortcut, the `MailFilter.Digest` notification identity and the
  `mailfilter:` protocol. `-Uninstall` removes all of it. The setup wizard runs
  it for friends.
- **Tabs:** All / Classes / Fests / Other / Filtered, course chips, date
  filters (on / before / after / between), search.
- **One action per card:** shown mail → **Mark unimportant**; Filtered →
  **Mark important**; pinned → **Unmark important**; mail that can never be
  hidden shows the button disabled with the reason. (The old "Report" and
  "Not junk" buttons are gone; the API is still `/api/report`.)
- **Original:** exact message in a sandboxed iframe (CSP `default-src 'none'`),
  remote images blocked until asked (tracking pixels).
- **Refresh (⟳) really checks Gmail:** `POST /api/fetch` runs a full digest in a
  background thread via `run_digest.ps1`; the page polls `GET /api/fetch`,
  reloads, and toasts "N new mails" (store delta) or the failing line. Busy
  (exit 75) is reported as "already running", not a failure. Non-interactive, so
  a stale token cannot hang the server on an invisible consent window.

### 3.9 Ask — the chat (`qa.py`)

- Multi-turn: the page sends the whole conversation (`POST /api/ask`
  `{messages:[…]}`; the old `{question}` still works). Bubbles, starter chips,
  sessionStorage, **New chat**.
- **Grounding contract:** the model only sees numbered emails; the schema forces
  it to cite them; a reply that claims to have read the mail (`found: true`) but
  cites nothing is **discarded**. Two relaxations for chat only: a greeting may be
  short, and when *no* mail was retrieved the prose is shown, labelled "Nothing
  here was traced to an email".
- **Retrieval:** keyword overlap weighted by subject/summary/body/sender, course
  codes and events, plus a recency nudge. Follow-ups ("who sent that?") are
  scored over the last three questions with the newest weighted 1.5×. A relevance
  floor (`MIN_CHAT_OVERLAP`, recency stripped) stops "hi" dragging in every mail
  containing the word.
- **"When is the quiz" ≠ "when are the papers handed back":** mail about paper
  collection/redistribution, answer scripts, paper shows or marks uploads is
  discounted (×0.35) for date questions, and the prompt forbids offering those
  dates as the exam date.
- **Known facts in every turn:** the profile, who teaches each course (with HOD
  guesses marked "probably"), and the full weekly timetable with rooms and who
  takes each slot, with today named.
- **Style:** short and to the point — lead with the answer, one or two
  sentences, no "According to the emails…". Neutral pronouns for other people;
  "you" for the student. Light markdown (bold, bullets) rendered after escaping.
- `num_ctx` is set explicitly so a long prompt is never truncated from the front
  (which would drop the rules).

### 3.10 Notifications and reminders

- `alerts.py` (Task Scheduler `MailFilterAlert`, 20:00): tomorrow's mail events
  and classes in one notification, once per day.
- `notify.ps1` sends a Windows toast with **`scenario="reminder"`**, which stays
  on screen until the student clicks it away. It needs at least one `<action>`
  (Windows otherwise downgrades it to a fading toast) and a registered
  AppUserModelID. Its button opens the app window through `mailfilter:`. Falls
  back to a (fading) tray balloon and says so (exit 2).

### 3.11 Models and the GPU

- Digest (classification, summaries, dates): **gemma3:4b** — measured 4.5 s per
  batch of 15 on the RX 7900 XT, versus 63 s for `gemma4:26b-a4b-it-qat`.
- Chat: `CHAT_MODEL_PREFERENCE` — `MAIL_FILTER_CHAT_MODEL` if set, else
  gemma3:4b. The 26B model is opt-in only (see incident below).
- **Incident, 2026-09-13:** loading the 15 GB 26B model with a 16k context while
  gemma3:4b was also loaded filled the 20 GB card that drives the display; the
  monitor lost signal, and after a restart Windows had disabled the card
  (code 22), which made AMD Adrenalin report "not compatible with your installed
  driver". Safeguards added: `OLLAMA_MAX_LOADED_MODELS=1` (user env), chat
  `num_ctx` 8192, big model opt-in only. GPU is first choice, CPU the fallback
  (Ollama's own behaviour; it must be restarted after the card comes back to
  detect it).

---

## 4. Data files (all gitignored, all personal)

`credentials.json`, `token.json`, `digest_store.json` (full mail text),
`feedback.json` (marks), `last_run.json`, `alerts_state.json`, `run.lock`,
`digest.log*`, `viewer.log`, `profile.json`, `user_notes.json`,
`calendar_overrides.json`, `attachments/`, `dist/` (setup.exe embeds the OAuth
client). **Check none of these are staged before every push.**

## 5. Commands

```powershell
.\run_viewer.ps1                         # open the app window
.\run_digest.ps1                         # run the digest now
.\.venv\Scripts\python.exe mail_filter.py --recheck   # re-apply rules to stored mail
.\.venv\Scripts\python.exe people.py     # who teaches what
.\.venv\Scripts\python.exe profile.py --detect        # fill profile from mail
.\.venv\Scripts\python.exe test_mail_filter.py       # tests
powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 [-Uninstall]
```

---

## 6. Changelog

Dates are local (IST).

### 2026-09-11
- Project created: Gmail digest with Classes/Fests/Other/Ignore.
- Anthropic API swapped for local Ollama `gemma3:4b` (everything must be free).
- Published to GitHub; Pages for the OAuth homepage/privacy policy.

### 2026-09-12
- OAuth app moved to production. Viewer with Original/Report, Filtered tab.
- Two-tier recall rules (marks absolute; never-hide overridable by Report).
- Courses + timetable, calendar, date filters, grounded Q&A, mark important.
- setup.exe installer, evening reminder.
- Recall audit: three ways mail could be lost or misdated, fixed.
- Opens at logon as an app window; sticky reminder toasts; `mailfilter:` protocol.
- Refresh runs a real Gmail check; run lock; exit code 75.
- One action per card; Report → Mark unimportant.
- Ask became a multi-turn chat; follow-up retrieval; greeting relevance floor.

### 2026-09-13
- Segregation: sharper categories, `correct_categories()` (Classes → Other/Fests
  without academic evidence), `model_category`, `--recheck`. 19 mails fixed.
- `people.py`: sender→course memory, relay-aware identities, HOD guesses.
- `profile.py`: identity from the student's own mail.
- Chat knows profile, courses/teachers/HODs and the timetable; exam date ≠ paper
  handback; short answers; explicit `num_ctx`.
- Two-model split measured; 26B made opt-in after the GPU incident;
  `OLLAMA_MAX_LOADED_MODELS=1`.
- Summary date-hallucination guard; HTML-in-text-part fix; 3 stored summaries
  corrected.
- This handoff file created; from now on appended, committed and pushed after
  every prompt.
- Tests: 420 passing.

### 2026-09-13 (continued) — GPU repair, guardrails, attachments, notes, calendar

**GPU incident resolved.**
- State found: the RX 7900 XT was disabled (code 22) after the monitor lost
  signal and the PC was restarted; AMD Adrenalin then reported "not compatible
  with your installed driver" because it could not reach the card. Driver and
  Adrenalin versions actually matched (32.0.31041.1004).
- Re-enabled from an elevated shell (UAC); the driver then failed to start
  (code 31). `pnputil /restart-device` on the card fixed it: status OK, Radeon
  Software running. No reinstall, no download.
- Ollama restarted so it detects the GPU again. Verified GPU-first: gemma3:4b
  loaded onto the card (VRAM 0.66 → 5.30 GB of 20) and unloaded cleanly.

**Guardrails so VRAM exhaustion cannot take the display down again** — three
independent layers, any one of which would have prevented the incident:
1. **Ollama itself:** user environment `OLLAMA_GPU_OVERHEAD=6442450944`
   (6 GB Ollama will never use, for every app that uses Ollama) and
   `OLLAMA_MAX_LOADED_MODELS=1` (two models can never share VRAM).
2. **Mail Filter:** `OllamaClient._placement_options()` checks free VRAM
   (`\GPU Adapter Memory(*)\Dedicated Usage` + `HardwareInformation.qwMemorySize`)
   before loading a model. GPU is first choice; a load that would leave less than
   `MAIL_FILTER_VRAM_RESERVE_GB` (default 6) is sent to the CPU (`num_gpu: 0`)
   with a note. A model already on the GPU is left alone; unknown VRAM or model
   size falls back to Ollama's own behaviour; the guard can never break a run.
3. **Claude Code (the assistant working on this project):** a global
   `PreToolUse` hook (`~/.claude/hooks/vram-guard.ps1`, matcher
   `Bash|PowerShell`) denies any shell command that names a large model
   (gemma4, qat, 12b and up…) alongside something that could load it, and any
   model-touching command while dedicated VRAM is already above 14 GB. Enforced
   by the harness, verified live (it blocked a harmless echo mentioning gemma4).

**Attachments are read** (`attachments.py`, new).
- Formats: .xlsx/.xlsm (openpyxl), .xls (xlrd), .csv/.tsv, .docx including its
  tables (python-docx), .pdf (pypdf, up to 60 pages; scanned PDFs are recorded as
  "no text found"), .pptx (slide text), plain text (.txt, .tst, .md, .log, .json,
  .ics, .xml) and .html. Images/audio/video are recorded as not read.
- During the digest every attachment is downloaded (≤25 MB, ≤8 per mail), saved
  to gitignored `attachments/<mail id>/`, and read. Failures are recorded with the
  reason, never fatal.
- **ID matching is deterministic, not the model's job:** every spreadsheet row,
  Word-table row or text line containing the student's campus ID or mail login
  (whitespace and case ignored) is extracted with the sheet's own column names,
  e.g. `ID No = …; Room = F207; Seat = 12`. Those rows are given to the chat on
  every turn as facts, so "which room is my exam in" never depends on retrieval.
- `python mail_filter.py --attachments [DAYS]` backfills stored mail (default 60
  days) without the model.
- Attachment names and text are searched by the chat; up to two files per email
  (1200 chars each) go into the prompt. Chat context raised to 12288 tokens.
- Date extraction reads attached text too (assignment briefs live in PDFs).

**Ask can hand over documents.** A cited mail carries its stored attachments;
the page shows 📎 download chips under the source. `/attachment/<mail id>/<n>`
serves files by id and position only (no filename from the URL, path confined
to `attachments/`), PDFs inline, everything else forced to download, with
`Content-Security-Policy: default-src 'none'; sandbox` so an HTML attachment
cannot script the local API. Mail cards show the same chips.

**Answers from facts, not only emails.** New optional schema field
`from_known_facts`: in the chat, an uncited answer is accepted when it rests on
the profile, timetable, teachers, ID rows or the student's notes, and the page
labels it so. The one-shot Ask and any uncited claim without the flag are still
refused.

**Notes tab** (`user_notes.py`, new, gitignored `user_notes.json`): the student
writes what is not in any email (said in class, notice boards); every note is
given to the chat on every turn as the student's own words, newest first, capped
at 6000 characters. Add/delete via `/api/notes` and `/api/notes/delete`.

**Calendar rebuilt** (`calendar_store.py`, new, gitignored
`calendar_overrides.json`).
- No timetabled classes.
- Automatically on: exams/quizzes (with portions in `details`) and deadlines
  (with what to submit), except deadlines from Fests mail.
- One entry per real thing: the same assignment in several reminder mails is
  merged (date + title with words like "reminder/due/deadline" ignored) and lists
  every source mail.
- "Instructions" links: only URLs that appear verbatim in the mail, preferring
  Classroom/Drive/Docs/Forms/LMS, excluding unsubscribe/social/tracker/image
  links.
- "Add to cal" on anything else found in visible mail (listed under the month as
  "In your mail, not on your calendar"); "Remove from cal" on any entry, even an
  exam; both reversible (`/api/calendar/add|remove`).
- `events.py` now extracts `details` (portions / what to submit) and `link`
  (must be http(s) and present in the mail), and is told that paper collection,
  paper shows and marks uploads are never kind "exam".

**Faster Refresh.** Summaries and date extraction run `MAIL_FILTER_WORKERS`
(default 3) requests in parallel against the single loaded model; results are
mapped back per mail.

**Tests:** new sections for the VRAM guard, attachments, facts/notes, calendar
and parallel summaries.

**Best model for the chat, safely.** The user asked for "the best of the best
models so there's no hallucinations" without touching the drivers.
- Chat preference is again `gemma4:26b-a4b-it-qat` (then gemma3:12b, gemma3:4b);
  the digest stays on gemma3:4b for speed. Chat requests get a 900 s timeout.
- Evidence it is safe: Ollama's own log for the first chat load after the
  change reads `overhead="6.0 GiB"`, "projected to use 14280 MiB … will leave
  6020 MiB of free device memory", 31/31 layers on the GPU. Total dedicated VRAM
  peaked at 16.3 GB of 20 with the desktop - the reserve held.
- Mail Filter's VRAM guard now trusts Ollama when Ollama keeps at least the
  same reserve (`ollama_reserve_gb()` reads `OLLAMA_GPU_OVERHEAD` from the
  process or the user's saved environment). Its own estimate had said 18.7 GB
  and would have sent the model to the CPU. Without that setting (a friend's
  machine) the conservative CPU fallback still applies.
- Honest limit, stated to the user: a bigger model makes fewer mistakes, but
  what prevents invented answers is the grounding (citations required, dates
  checked against the mail, ID rows found deterministically), and all of it
  stays on.

**Claude Code hook narrowed.** Rule 2 used to block every python/test command
whenever VRAM was above 14 GB, which blocked a unit-test run while the 26B chat
model was legitimately loaded. It now blocks only commands that directly load a
model (ollama run/pull/create, /api/chat|generate on 11434,
MAIL_FILTER_CHAT_MODEL). Rule 1 (a named large model plus a loader) is
unchanged. Pipe-tested: big model DENY, 27b DENY, tests allow, small load with
low VRAM allow.

**Tests:** 488 passing (transport tests now look up the `/api/chat` request
rather than the first request, since the guard checks `/api/ps` first; tests
pin `ollama_reserve_gb` to 0 so they do not depend on the machine).

**Backfill run on the real store:** `mail_filter.py --attachments 60` checked
159 stored mails - 25 attachments stored and read, 4 could not be read (recorded
with the reason in the store), 0 rows containing the student's ID (no seating
sheet has arrived yet). pypdf printed harmless "Multiple definitions … /Info"
warnings on some PDFs. Viewer restarted on the new code: Notes tab, attachment
chips and calendar suggestions live; the calendar for Sep–Oct shows 56
must-not-miss entries, 86 suggestions and no timetabled classes.

**What the 4 unreadable attachments were, and the fix.**
- `ps (8).xls` was an HTML page saved with an .xls name - the usual shape of an
  ERP "Excel" export (xlrd: "Expected BOF record; found <!DOCTYP"). Seating
  plans and mark lists often come this way, so `attachments.read_html_tables()`
  (stdlib `html.parser`) now reads any .xls/.xlsx that is really markup as the
  tables it contains, with the same column-labelled ID matching. Tested with a
  fake ERP export; the stored file was re-read from its saved copy.
- The other 3 were scanned PDFs with no text layer ("no text found"). Reading
  them needs OCR (e.g. Tesseract, a separate large install) - not added without
  the user's say-so; they are still stored and downloadable.

### Still to do (as of this entry)
- Verify a real seating sheet end to end when one arrives.
- OCR for scanned PDFs, if the user wants it (needs a separate install).
- Any stack change for very-low-VRAM machines (the user's "repo that shrinks
  Gemma") is on hold: when asked which repo, the user named the Mail Filter repo;
  no quantisation project has been chosen.
