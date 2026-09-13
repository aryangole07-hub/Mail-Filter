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

### 2026-09-13 — HSS deadlines in orange

The student asked for "anything from any HSS course that's due" to be orange,
not red, on the calendar.
- `ui.html`: `isHssDue(e)` is true for a `deadline` whose mail is tagged with an
  `HSS …` course code (currently HSS F352 TWS and HSS F222 Linguistics). Such
  entries get the `hss` class on the day pill and on the "Due" tag in the day
  detail and suggestions list; the rules sit after the red exam/deadline rules
  so they win. New colour token `--hss`: `#c2410c` in the light theme,
  `#fb923c` in both dark-theme blocks. Legend: "Due for an HSS course".
- Deliberately unchanged: quizzes/exams in HSS courses stay red ("due" read as
  deadlines), and deadlines from every other course stay red.
- Tests cover the selector, the class, rule order, all three theme tokens, the
  legend, and that the registry's HSS codes are the two expected.

### 2026-09-13 — nothing counted twice on the calendar

The student asked to make sure no event, quiz, exam or due date is counted
twice. Listing every same-day group in the live calendar showed it was worse
than wording differences:
- the FoFA Quiz 1 appeared **four times** (31 Aug, 1 Sep, 2 Sep, 16 Sep) - a
  "quiz marks not showing" reply thread had been read as the quiz, dated with
  the thread's own dates; the real date is 16 Sep 18:15;
- same-day twins: "FoFA Quiz 1" + "FoFA Quiz"; "Outstation Leave or Overnight
  Stay Permission" + "Outstation Leave / Overnight Stay Permission";
- non-events on the calendar as exams/deadlines: a polo shirt, a cap, a
  library book display, the mediclaim policy, "Quiz 2 paper distribution",
  library facilities ("Renewal facility"), and midsem/compre dated 11 Sep from
  a mail that gives no dates at all.

`calendar_store.py` was rebuilt:
1. **Merging by what a thing is.** A mention's course comes from its title
   (code, name, aka, short forms - "FoFA", "ECON F212", "Fundamentals of
   Finance…" are one course; two-letter forms like "DE"/"TS" are ignored as too
   ambiguous), else from the mail if it has exactly one course. Course + quiz /
   test / assignment / tutorial / project number ("Quiz-1", "Quiz 1", "first
   quiz", "Quiz I") is one identity across **all dates**; midsem and compre are
   once per course. Mentions without an identity merge with a same-day,
   same-kind, same-course mention whose title tokens overlap ≥60 % or contain
   one another (course names, numbering and words like reminder/due/re/fwd are
   normalised away).
2. **Which date wins.** The date written in the mail (+3), a relative phrase
   such as "tomorrow" (+1), a start time (+2), details (+1), then the most
   recently received mail (so a postponement wins). Start/end/location come only
   from mentions on the winning date. Every source mail is listed; the other
   dates are kept in `other_dates` but never shown as entries.
3. **Only grounded, genuine items go on automatically.** `validated_kind()`: an
   exam needs exam wording and no paperwork/marks/serial-number wording; a
   deadline needs submit/pay/register-type wording (title or details); otherwise
   the kind becomes "other". The event's date must appear in the mail (day +
   month name, dd/mm, yyyy-mm-dd; subject, text, HTML and attachment text are
   searched) or the mail must use relative wording. Failing items are still
   offered under the month with Add to cal.
4. Merging happens across all stored mail **before** the month range is
   applied, so a wrong-dated mention cannot survive because the real date is in
   another month. Add/Remove act on every key of a merged entry.
5. `alerts.py` evening reminder now uses the same merged entries (minus anything
   removed from the calendar), so one quiz is one line there too.

Result on the real store (Aug–Dec): **59 → 12** entries on the calendar; the
FoFA Quiz 1 is one entry on 16 Sep 18:15 with four source mails and three
misread dates recorded; Assignment I (MATH F201) is one entry on 15 Sep 17:00.

A second pass fixed the leftovers that listing showed:
6. **Kinds only have to agree for exams and deadlines.** "Tut - 6" labelled
   "meeting" in one mail and "other" in another, and "Class participation"
   three times a day, were kept apart only by the model's inconsistent labels.
7. **Folding across dates (`_fold_across_dates`).** Same-titled mentions on
   different dates (token overlap ≥80 %, same course, compatible kind; titles
   shorter than three tokens only within one mail or one thread, so two
   unrelated "Registration"s stay apart): every date that is *written* in a
   mail stays its own entry - real schedules such as presentations on the 21st,
   23rd and 25th or a vaccination running 13–18 Sep survive - and mentions on
   dates no mail writes are folded into the nearest written one. If no date in
   the cluster is written anywhere, it becomes one entry.
8. The page shows folded dates on the entry: "Also mentioned for 31 Aug – shown
   once, on the date the mail gives".

After the second pass the real calendar showed **8** entries and 130
suggestions; two same-day twins were left, and a third pass fixed them:
9. **Same day, same wording, different label.** "Face Scan Registration" and
   "Assignment presentation" each appeared once as a deadline (on the calendar)
   and once as a meeting (suggested). Same-day mentions now merge across kinds
   when their wording is near-identical (≥80 % token overlap); the merged entry
   takes the most important kind (exam > deadline > other).
10. **Date lists.** "Presentations on 21st, 23rd and 25th September" only
    counted the 25th as written, so the 21st and 23rd were folded away.
    `date_mentioned` now reads days listed before a month (",", "and", "&",
    "to", "-").

### 2026-09-13 — second crash; local models locked to the CPU for good

While probing the timetable-screenshot reader, the PC hard-crashed again
(Kernel-Power 41 at 06:23, the second unexpected reboot that day after 05:15),
and Windows came back with the RX 7900 XT disabled (code 22). Ollama's log shows
the crash happened with **gemma3:4b using ~2.7 GB of 20 GB VRAM** (all 35
layers plus the vision encoder on ROCm0). So the earlier conclusion was wrong:
this is not VRAM exhaustion - the AMD ROCm compute path itself crashes this
machine when the display card runs a model. VRAM reserves cannot prevent that.

The user: "MAKE SURE IT NEVER HAPPENS AGAIN... NO MATTER WHAT." Policy now:
**local models run on the CPU only**, enforced by four independent layers:
1. **Ollama cannot see a GPU:** user environment `HIP_VISIBLE_DEVICES`,
   `ROCR_VISIBLE_DEVICES`, `GGML_VK_VISIBLE_DEVICES`, `CUDA_VISIBLE_DEVICES`
   = -1 and `OLLAMA_LLM_LIBRARY=cpu`.
2. **Ollama has no GPU code to load:** its backend folders `rocm_v7_1`,
   `vulkan`, `cuda_v12`, `cuda_v13` were moved out of
   `%LOCALAPPDATA%\Programs\Ollama\lib\ollama` into
   `…\Ollama\gpu-backends-disabled\` (with a README saying why). An Ollama update
   may reinstall them; layer 4 catches that.
3. **Mail Filter always sends `num_gpu: 0`.** `OllamaClient.create` only runs
   the VRAM placement guard when `MAIL_FILTER_ALLOW_GPU=1` is set on purpose;
   otherwise every layer stays on the CPU even if Ollama could see the card.
   Tests: every request carries `num_gpu: 0`; no `/api/ps` placement call by
   default; the opt-in path still runs the VRAM guard.
4. **Claude Code hook (`~/.claude/hooks/vram-guard.ps1`) rewritten:** denies any
   Ollama/model/python command unless layers 1 and 2 are intact; denies any
   command that would change those variables, move the backends back, set
   `MAIL_FILTER_ALLOW_GPU`, or request GPU layers; still denies commands naming
   large models. Pipe-tested 6/6 (big model deny, 27b deny, undo-variable deny,
   move-backends-back deny, tests allow, small CPU model allow).

The card was re-enabled (code 22 → 31) and fixed with an elevated
`pnputil /restart-device` → OK. Cost of the policy: model calls are slower (CPU,
32 GB RAM); the 26B chat model remains in the preference list but runs on the
CPU. RoundTable shares this Ollama install, so it is CPU-only too.

### 2026-09-13 — chat answers were empty: the model thought itself out of tokens

The user asked for three chat checks (next exam with its source email; seating
or room assignments; HSS teachers, HOD and Wednesday classes). All three came
back "The local model did not answer" after ~4 minutes each. Ollama's log showed
HTTP 200 and no truncation, so the failure was on our side. A diagnostic call
printed the raw reply: **empty content, done_reason "length"**, prompt ~5.7k
tokens. `gemma4:26b-a4b-it-qat` is a thinking model and spent the entire
900-token answer budget on hidden thinking before writing any JSON.

Fix: `OllamaClient.create` now sends `"think": false` for models whose
`/api/show` capabilities include "thinking" (asked once per model and cached);
other models (gemma3:4b) are not sent the flag, since non-thinking models reject
it. Tests: no flag for a plain model, `think: false` for a thinking model, and the
capability lookup happens once.

### 2026-09-13 — Smart conflict & overlap detector

Requested feature: cross-reference extra classes or rescheduled labs from mail
against the weekly timetable and alert when a makeup class or test clashes with
an existing course slot.

`conflicts.py` (new, deterministic - no model):
- `clashes_for(entry)` lays a timed calendar item against `courses.classes_on`
  for its date. It clashes when its time overlaps a slot of a **different**
  course (a FoFA quiz in the FoFA lecture is not a clash). An item without an
  end time is taken to last 50 minutes (one BITS slot); touching end/start
  times do not count; items without a time and weekends cannot clash.
- `is_extra_class` recognises makeup / extra / additional / rescheduled /
  compensatory / special / replacement classes, so the warning can say
  "Extra class clashes with …".
- `annotate(entries)` adds `clashes` (course label, type, times, room) and
  `extra_class`; `describe(entry)` gives one plain sentence.

Wired in:
- `viewer.calendar_entries` annotates both calendar entries and suggestions.
- `ui.html`: a red "⚠ Clashes with Linguistics lecture 10:00–10:50 (J Block
  J217)" line in the day detail, and a ⚠ prefix plus tooltip on the month pill.
- `alerts.py`: the evening reminder puts clash warnings for tomorrow first.

Tests cover a makeup class over another course's lecture, own-course overlap,
default slot length, touching times, untimed items, weekends, and the wording.

### 2026-09-13 — To-Do list

Requested: extract pending tasks from mail into a To-Do section with
checkboxes, let the student add their own, reorder by drag and drop, plus any
other UI/UX that helps (examples: "Fill Google form by 5 PM", "Upload PPT before
tutorial").

`todo_store.py` (new, no model; `todos.json` gitignored):
- **Mail tasks** are rebuilt from the mail on every load: every deadline from
  `calendar_store.build` (already merged, so one assignment is one task, with its
  details and instruction links), plus **action sentences** - an instruction verb
  aimed at the student (fill, submit, upload, register, pay, bring, attend, …,
  optionally after "please / kindly / you must / all students must") with a
  limit ("by / before / until / no later than / within …"), at most 3 per mail,
  never from filtered (Ignore) mail.
- **Your tasks**: text plus optional due date/time; newest at the top.
- **State survives rebuilds** through stable keys: ticking a mail task off,
  renaming it, or dragging it keeps; removing a mail task dismisses it so it does
  not come back. Unplaced tasks sort soonest-due first. A mail task more than 14
  days past its due date and never ticked off is dropped, so the list stays
  about what is still live; ticked-off tasks stay under Done.
- Each item carries `overdue` / `due_today`.

Tuned on the real store: the first version listed 15 open tasks, most of them
placement openings, hackathon registrations and deadlines up to 13 days past.
Mail tasks now come only from deadlines that are **on the calendar** (so the
calendar's must-not-miss judgement and the student's Add/Remove choices decide
what becomes a chore - and internship/placement mail, which the student asked to
leave alone for now, no longer does), and a mail task more than **3 days** past
due and never ticked off drops off (was 14). Own tasks never drop off.

Viewer API: `GET /api/todos`; `POST /api/todos/add|update|delete|order`.

UI (new **To-Do** tab): add box with optional date and time (Enter adds);
checkbox to tick off; click the text to rename (Enter saves, empty reverts);
✕ to remove; drag the ⋮⋮ grip to reorder, or Alt+↑/↓ from the keyboard; due
badges ("Overdue", "Today" in orange, "Due Mon 14 Sep 17:00"); a "from mail"
chip that opens the source mail and instruction links; "Show done" toggle and an
open/done count.

Tests: action sentences found (two in one mail), none without a limit;
deadlines become tasks; junk mail sets none; own task added at top; empty
refused; tick-off survives rebuild; removed mail task stays gone; drag order
kept; rename; overdue flag; corrupt file.

### 2026-09-13 — the user's three chat checks, after the thinking fix

Asked through the real viewer, on the CPU (gemma4:26b-a4b-it-qat, ~2–2.5 min
each, prompt ~7–8k tokens at ~62 tokens/s, generation ~8.6 tokens/s):
1. *Next exam or quiz, with source subject and sender* - "Your next quiz is FOFA
   Quiz-1 on 2026-09-16 at 18:15", citing "Regarding FOFA Quiz-1" from Utkarsh
   Kumar. Correct, and not a paper-collection date.
2. *Seating or room numbers in any mail or spreadsheet* - "I couldn't find any
   information regarding your specific exam seating arrangements or assigned
   room numbers". Correct: no attachment row contains the campus ID yet.
3. *HSS teachers, HOD, Wednesday classes* - Ufaque Paiker (TWS) and Pranesh
   Bhargava (Linguistics), correct; HOD "I don't have information" (people.py has
   no guess for these courses - too few class-wide mails), honest; Wednesday
   classes at 09:00, 10:00, 14:00, 16:00, 17:00 - correct times, courses not
   named. Flaw: returned found=false while citing 6 mails, so the page styles it
   as not found; worth tightening.

### 2026-09-13 — Marks tracker and CGPA simulator

`marks.py` (new, no model; `marks.json` gitignored):
- Reads released marks from mail text only when the mail is about marks
  (marks/score/grade/result/evaluation/CGPA wording) and only when the number is
  clearly a score: with a maximum ("Quiz 1: 8/10", "8 out of 10") or right after
  "marks"/"score" ("Midsem marks: 34"), or "you scored/secured/got X (out of Y)
  in <component>". So "Assignment 2: 25 September" and "Total: 500 rupees" are
  not marks; a score above its maximum is rejected; a missing maximum stays
  unknown.
- Reads marks from attached mark sheets: in the row that contains the student's
  ID, any numeric column named like a component or "marks"/"score"; the maximum
  comes from the column name ("Quiz 1 (10)", "Midsem [40]", "Max 20").
- On the real store the tracker finds **no marks**, which is correct: the only
  marks-related mails are "marks not showing up" complaints with no scores.
- Components normalised (Midsem, Compre, Quiz N, Assignment N, Lab, EC N, …);
  course from the sentence, the attachment filename, the subject, or the mail's
  single course tag. One entry per course + component: newest mail wins; a mark
  you enter wins over mail; removing a mail mark hides it for good.
- Simulator on BITS grade points (A 10 … NC 0): per-course units (default 3)
  and expected grade → unit-weighted semester GPA and new CGPA folded into your
  CGPA and units so far; and the average grade points a target CGPA needs.

Viewer: `GET /api/marks`; `POST /api/marks/add|remove|course|history`.
UI: new **Marks** tab - CGPA-so-far card, live "if you get these grades" CGPA,
"what a target needs" (with a rough letter grade, "not reachable" / "already
safe"), a course table with marks so far, units and expected-grade dropdowns,
an add-mark form, and a marks list with a progress bar, source chip that opens
the mail (evidence shown on hover) and remove.

### 2026-09-13 — Timetable screenshot import (for the friends' setup)

The student sent the ERP weekly "Schedule" screenshot (every Display Option
ticked). It matches `courses.WEEKLY` exactly - all 27 slots - so it is the ground
truth for measuring the importer. (An earlier probe of this on the GPU crashed the
PC; everything here ran on the CPU.)

`timetable_import.py` (new):
- **Finding boxes.** A first probe found 2 "boxes" instead of 27: side-by-side
  classes in the ERP grid are divided only by a one-pixel very light line
  (≈ RGB 223,239,203) against box fill ≈ 183,209,146, and the first colour test
  counted the line as box colour, merging whole rows. `_fill()` excludes pixels
  that light; columns are found by how much box colour each x holds; rows inside
  a column by the share of the column's width that is box colour (text only
  covers part of a row).
- **Weekdays.** The grey grid lines are covered wherever classes sit side by side,
  so `column_edges()` alone found only Time/Saturday/Sunday lines. `_boundaries()`
  adds the gaps between boxes; with all 8 columns (Time + 7 days) measured, each
  box goes to the column holding its centre. If they cannot be measured, boxes are
  taken left to right as Monday onwards and a warning says so.
- **Real screenshot, no model:** 27/27 boxes; Mon 5, Tue 6, Wed 5, Thu 5, Fri 6 -
  every day count correct.
- **Reading.** Each box is cropped, doubled in size and transcribed by gemma3:4b
  through `OllamaClient` (CPU only, ~40 s a box). The CPU probe showed verbatim
  transcriptions. `parse_cell()` then extracts code, section, type, 12→24-hour
  times, room ("F Block F207"), instructors (split, trailing "." removed,
  title-cased) and course name, and lists missing fields; `import_screenshot()`
  turns those into warnings naming day, time and course, and flags two classes
  read at the same time.
- `build_timetable()` → `timetable.json` shape: courses (known codes keep their
  short forms and names; unknown ones get an acronym), weekly slots, per-slot
  instructors. `python timetable_import.py <image> [--save]` runs it by hand.
- Not yet: `courses.py` loading `timetable.json`, and the setup wizard using it.

### Still to do (as of this entry)
- Verify a real seating sheet end to end when one arrives.
- OCR for scanned PDFs, if the user wants it (needs a separate install).
- Chat: answers that cite mail but report found=false are styled "not found".
- Friends' setup: courses.py loads timetable.json; wizard with details +
  timetable picture + continue-regardless warning; macOS support (requested).
- Friends' one-click setup.exe with the timetable-screenshot import (requested,
  in progress: screenshot saved, box detection and reading still to build and
  test on the CPU).
- Old events were extracted before `details`/`link` existed and with the older
  prompt; re-extracting dates for stored mail would improve portions/links
  (model time on gemma3:4b) - not done yet.
- Any stack change for very-low-VRAM machines (the user's "repo that shrinks
  Gemma") is on hold: when asked which repo, the user named the Mail Filter repo;
  no quantisation project has been chosen.

---

## 2026-09-13 — macOS support, the friends' setup for Windows and Mac, own timetables

Prompt: "continue. i want to give the app to my friends that use mac os also.
make it compatable with that also".

### macOS support
- **`platforms.py` (new)** holds everything that differs by system:
  - Where the app lives:
    - Windows: `%LOCALAPPDATA%\MailFilter`
    - Mac: `~/Library/Application Support/MailFilter`
    - Linux: XDG data folder
  - The venv's Python, and the flags that hide console windows.
  - **App window:** finds Chrome, Edge, Brave or Chromium and opens
    `--app=http://127.0.0.1:8765/`. On a Mac it runs
    `open -na "<browser>" --args --app=… --new-window`; with no such browser it
    falls back to the default browser.
  - **Sticky reminder on a Mac:** `osascript display alert` stays on screen until
    it is clicked (Dismiss / Open Mail Filter). The text is AppleScript-escaped,
    so quotes in a mail title cannot break out. Linux uses
    `notify-send --urgency=critical`.
  - **LaunchAgents** in `~/Library/LaunchAgents`, the Mac version of Task
    Scheduler:
    - `com.mailfilter.digest` runs every day at 07:55.
    - `com.mailfilter.alert` runs every day at 20:00.
    - `com.mailfilter.viewer` opens the app at login, 30 s after sign-in.
    - PATH includes `/opt/homebrew/bin` and `/usr/local/bin`, so Ollama is found.
    - Plist paths are joined with "/" even when built on Windows, so a path with
      spaces ("Application Support") stays one argument.
    - `install_launch_agents` uses `launchctl bootstrap`, falling back to
      `load -w`. There is also `uninstall_launch_agents`.
    - Command line: `python platforms.py install-launch-agents | uninstall-launch-agents | open-window`.
- **Mac launchers (new, LF line endings; `.gitattributes` keeps them LF):**
  - `run_digest.sh` starts Ollama.app (or `ollama serve`) if needed, runs the
    digest, rotates the log and writes the same run markers as Windows.
  - `run_viewer.sh` starts the viewer once (port and process check) and opens
    the app window.
  - `run_alert.sh` runs the evening reminder.
  - `install_autostart_mac.sh [--uninstall]` installs or removes the three
    LaunchAgents.
- **`alerts.py`:** off Windows, the reminder is shown with
  `platforms.notify_sticky`.
- **`viewer.py`:** off Windows, the refresh button runs `run_digest.sh`.
- **GPU policy (`mail_filter.py`):**
  - Windows and Linux stay CPU-only (`num_gpu 0`), exactly as before. The
    lockout on this PC is untouched.
  - On a Mac the model may use Apple's unified-memory GPU. There is no separate
    video memory to exhaust and no display driver to break. `MAIL_FILTER_CPU_ONLY=1`
    keeps a Mac on the CPU too.
  - Tests check all three cases.

### Friends' setup, both systems
- **`setup_wizard.py` (rewritten, cross-platform)**, four screens:
  1. **Welcome.**
  2. **Name + BITS ID.** The mail login, campus and batch are worked out from
     the ID: `2025B3PS0420H` gives `f20250420@hyderabad…`, and the Pilani, Goa
     and Dubai domains are handled too. An empty name or a malformed ID shows a
     yellow warning and the button becomes **Continue regardless**.
  3. **Timetable picture.** Step-by-step ERP instructions (tick every Display
     Option, screenshot the whole week, Win+Shift+S / Cmd+Shift+4), then
     **Choose picture…**. With no picture it warns once, then **Continue regardless**.
  4. **Install.** Everything else is automatic:
     - Copies the app.
     - Finds or installs Python (Windows).
     - Creates the venv and installs requirements.
     - Installs Ollama: `OllamaSetup.exe` silently on Windows; on a Mac,
       `Ollama-darwin.zip` unpacked with `ditto` into `~/Applications`.
     - Starts Ollama and downloads gemma3:4b with a progress bar.
     - Writes `profile.json` (name, ID, email, campus, batch).
     - **Reads the timetable picture** (`timetable_import.py --save --report`,
       progress per box).
     - Opens Google sign-in (first 14-day backfill), then `profile.py --detect`.
     - Schedules the jobs: schtasks + `install_autostart.ps1` on Windows,
       `install_autostart_mac.sh` on a Mac.
     - Creates a desktop shortcut: `.lnk` on Windows, `Mail Filter.command` on a Mac.
     - Opens the app.
- **Timetable warnings mid-install:** if any box is missing a field, the
  install pauses on a screen listing exactly what is missing, e.g.
  "Friday 14:00 (HSS F352): could not read the room". The choices are
  **Continue regardless** or **Choose another picture…**, which reads the new
  picture instead.
- **Fixed:** `APP_FILES` was stale. It lacked people.py, profile.py,
  attachments.py, calendar_store.py, conflicts.py, todo_store.py, marks.py,
  user_notes.py, the timetable importer and platforms.py, so a friend's install
  would have crashed. The build now refuses to run if the wizard installs a file
  the build does not ship (tested).
- **`Install Mail Filter.command` (new):** the Mac entry point, a right-click →
  Open. It looks for a Python 3.10+ with tkinter (python.org framework builds,
  Homebrew, PATH). If there is none, it downloads the official python.org
  installer, opens it and says to run the installer again afterwards. Then it
  runs the wizard.
- **`build_setup.py`** now builds:
  - `dist/setup.exe` (Windows, PyInstaller, as before but with the full payload).
  - `dist/MailFilter-mac.zip`: a "Mail Filter Setup" folder with the app, the
    wizard, the `.command` and `HOW TO INSTALL.txt`. Zip entries carry Unix
    permissions: scripts 755 and LF, other files 644.
  - `--exe` or `--mac` builds just one. Both embed `credentials.json`: share
    them directly, never publicly.
- **`SETUP-FOR-FRIENDS.md` (new):** very plain instructions:
  - Before you start: the ERP screenshot with every Display Option ticked, and
    your ID.
  - Windows steps, including "Windows protected your PC" → More info → Run anyway.
  - Mac steps, including the right-click → Open for unknown developers and the
    Python installer.
  - The Google "hasn't verified this app" → Advanced → Go to Mail Filter step.
  - What each warning means, and what happens after setup.

### A friend's own timetable
- **`courses.py`** loads `timetable.json` at import when it exists. It replaces
  COURSES, BY_CODE, WEEKLY and SLOT_PROFS and recompiles the course matchers, so
  every module sees the friend's own courses, rooms and instructors. Malformed
  courses, bad days and classes for unknown courses are dropped, and an
  unreadable file is ignored. `timetable.json` is gitignored. This repo has no
  such file, so the owner's built-in timetable is unchanged (tested).

### Timetable reading, measured
- **Full CPU run on the real screenshot:** 27 boxes in 600 s, scored field by
  field against the known timetable:
  - day+start 27/27; code, section, type, end time and room all 27/27.
  - **instructors 20/27.** Two names ran together (the model dropped the ".,"
    separator), one name had a word in Bengali script, and "Pranesh" was read
    as "Praneesh".
- **Fixes in `timetable_import.py`:**
  - `read_instructors` rejoins the wrapped lines after "Instructors:" and splits
    them at the separators.
  - `snap_name` matches misspelt names to known instructors (difflib ≥ 0.85) and
    splits run-together names into known people, longest match first. Unknown
    instructors are kept as read.
  - `tidy_instructors` gives every box of the same class (course + section) the
    reading most boxes agree on. It prefers readings without non-Latin letters,
    and cleans and flags a name that is still garbled.
  - The prompt now asks for commas between instructors.
- **Re-scored on the saved run (no model): instructors 27/27, no warnings.**

### Tests
- 642 passed, 0 failed. New checks cover:
  - LaunchAgents: schedule, login delay, path with spaces.
  - AppleScript escaping, Linux notify, the Mac app-window command.
  - GPU policy per system; LF endings for the `.sh`/`.command` files and `.gitattributes`.
  - ID → email/campus, the details warnings, payload completeness.
  - Mac zip contents, permissions and line endings; no credentials when none are given.
  - Loading a personal timetable and discarding bad data.
  - Instructor rejoining, snapping, splitting, agreement and garbled-name warnings.

### Not verified
- **Nothing has been run on a real Mac** (only this Windows PC is available).
  The shell scripts pass `bash -n`, and the plists, commands and zip are unit
  tested. The first friend's Mac install is the real test: if something fails,
  their "Setup could not finish" message says which step.
- The mid-install timetable warning screen and the Mac Ollama unzip are
  untested end to end.

### Still to do (as of this entry)
- Run the Mac installer on a real Mac and fix whatever it reports.
- Rebuild `dist/setup.exe` and `dist/MailFilter-mac.zip` with
  `build_setup.py` before sharing.
- Verify a real seating sheet end to end when one arrives.
- OCR for scanned PDFs, if wanted.
- Chat: answers that cite mail but report found=false are styled "not found".
- Old events lack `details`/`link` (re-extraction not done).

---

## 2026-09-13 — Built the setup files to send to friends

Prompt: "build the setup files so i can send them. also show me what to send to
the mac user so she can set everything up".

- **Built** with `build_setup.py`:
  - `dist/setup.exe` (≈11.4 MB, Windows).
  - `dist/MailFilter-mac.zip` (≈0.2 MB).
  - Both embed `credentials.json`; `dist/` is gitignored.
- **Copied** to `Downloads\Mail Filter - send to friends\` together with
  `SETUP-FOR-FRIENDS.md`.
- **Zip checked:** a "Mail Filter Setup" folder with every app file,
  `credentials.json` and `HOW TO INSTALL.txt`. Scripts are 755, other files 644.
- **Mac instructions updated for macOS Sequoia.** Right-click → Open no longer
  bypasses Gatekeeper there. The guide now says: double-click → Done → System
  Settings → Privacy & Security → **Open Anyway**. Right-click → Open is kept
  for older macOS.
- **Build fix:** the guide inside the zip was stored as 600; it is now 644.
- **Reminder for sharing:** if the Google Cloud OAuth app is still in
  *Testing*, each friend's email must be added under OAuth consent screen →
  Test users, or their sign-in is refused. BITS Google Workspace admins may
  also block unverified third-party apps; that is outside the app's control.

---

## 2026-09-13 — Adding a Mac friend as an OAuth test user

Prompt: add a friend's BITS email to the Google OAuth test users.

- Google has no API or gcloud command for OAuth consent-screen test users
  (and gcloud is not installed here), so it cannot be done from the terminal.
- Opened the Google Auth Platform **Audience** page for project
  `bubbly-card-508320-m3` in the browser; the user adds the address under
  **Test users → Add users → Save**.
- Test users are capped at 100 while the app is in Testing.

- Checked: the OAuth app is **In production**, External, unverified, user cap
  1/100. No test users are needed - any Google account can sign in after the
  "Google hasn't verified this app" → Advanced → Go to Mail Filter screen,
  up to 100 users over the project's lifetime (each friend uses one slot).
