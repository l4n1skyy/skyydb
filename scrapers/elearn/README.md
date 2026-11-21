# elearn

Log into Sunway eLearn (Blackboard Ultra) with Playwright and scrape the activity
stream (`https://elearn.sunway.edu.my/ultra/stream`). Goal: feed a dashboard that
summarises courses, deadlines and announcements.

## Setup

| What | Where |
|---|---|
| Project | `scrapers/elearn/` in skyydb (`elearn.py`, `pyproject.toml`, `uv.lock`) |
| Python env | `~/.local/share/venvs/elearn` (Python 3.12, `playwright==1.65.0`), made with `uv` |
| Browser | the system `/usr/bin/chromium`, driven by Playwright (no Playwright browser download) |
| Browser profile | `~/.local/share/elearn/profile`: cookies, so a login survives between runs |
| Credentials | `~/.config/elearn/credentials`, mode 600: `ELEARN_USER=...` and `ELEARN_PASS=...`. skyydb's **Work → Settings** writes it |

The venv, the browser profiles and the credentials live outside skyydb's folder: the password
must never end up in git, and if the folder sits on a drive without Unix permissions (NTFS, say)
a file there can't be kept private (nor can it hold a venv's symlinks). Don't put
`~/.config/elearn` in a dotfiles repo either.

### Set it up

```bash
cd scrapers/elearn   # in skyydb's folder
UV_PROJECT_ENVIRONMENT=~/.local/share/venvs/elearn uv sync
```

Then save your Sunway ID and password in **Work → Settings** (or write the credentials file
yourself with `chmod 600`).

## Usage

Run with the project's interpreter (`-I` so nothing in the working folder is imported):

```bash
PY=~/.local/share/venvs/elearn/bin/python
$PY -I elearn.py manual   # open the login page; YOU type the credentials (nothing is filled in)
$PY -I elearn.py login    # log in with the stored credentials, browser stays open
$PY -I elearn.py sync     # scrape, then add the new items to skyydb's elearn.db
$PY -I elearn.py stream   # headless scrape; saves stream.json + stream.html (add --show to watch)
```

Closing the browser window ends `manual` / `login`.

## How the login works

The site is a two-step login (found by probing it):

1. `https://elearn.sunway.edu.my/ultra/stream` redirects to the Blackboard login page.
2. A "Privacy, cookies and terms of use" dialog covers it: click `#agree_button`.
3. Click the **Sunway ID** button. This leaves Blackboard for an ADFS page at
   `pinwheel.sunway.edu.my/adfs/ls/`.
4. ADFS form: `#userNameInput`, `#passwordInput`, **Sign in** is `#submitButton` (a span).
   The "keep me signed in" box (`#kmsiInput`) is styled invisible and can't be clicked, so
   it's skipped; the persistent profile keeps the cookies instead.
5. Back on Blackboard, possibly with an MFA code prompt (`#totp-verification-input`).
   The script waits for you to enter it in the window.

If the session cookie is still valid, step 1 lands straight on the stream and no login happens.

## In skyydb

- Data: `elearn.db` in skyydb's folder (its own database; schema in `schema/elearn.sql`).
  `stream_items` has one row per stream item (kept after it leaves the stream, de-duplicated by a
  hash of course + title + content + posted time); `scrapes` logs every sync.
- Page: the **Announcements** tab inside Work (`http://127.0.0.1:8001/work?view=announcements`; `/work/announcements` redirects there).
  Items are split by what they are, from the words in the title: **Deadlines** ("Due:"), **Grades**
  ("Grade posted:", with the score and a %), **Announcements**, **New content** ("Added:", one block per
  course, tagged Lecture / Practical / Assessment / Recording / File from keywords in the name),
  **Courses added** ("New course available") and Other. Filter by course; **Refresh** runs
  `elearn.py sync` in the background and the page reloads itself when it's done. Raw rows: `/elearn-db`.
- `posted_at` is worked out for items the stream dates as "5 hours"; `due` is read from
  "Due Date: 11/15/26, 11:59 PM". `kind` is deadline / grade / announcement / content / enrolment / other; grades also get a `score` like `22 / 25`.
- The app files: `plugins/app.py` (`announcements*`, `elearn_credentials_save`, `elearn_sign_in`),
  `templates/app_announcements.html`, `bin/skyydb` (feature `elearn`), `datasette.yaml`.
- **Work → Settings** has the sign-in: your Sunway ID and password (written to the credentials
  file, the password never shown again), **Sign in to eLearn** (a window, for when eLearn wants a
  code) and **Sign in to Outlook** (a window: approve on your phone).

## Status

- Login works with the stored credentials. The session cookie doesn't survive closing the
  browser, so every run signs in again.
- `stream` signs in, scrolls to load older items, then saves `stream.html` (raw, for debugging)
  and `stream.json`: `{scraped_at, items: [{group, date, time, course, title, content, due,
  link}]}`. `group` is Upcoming / Today / Recent. First run: 124 items (2 upcoming, 1 today).
- `date` can be a relative value ("5 hours", "22 hours") for recent items, with the real
  clock time in `time`. Normalising that is part of the next step.
- Chromium runs with its sandbox on (`chromium_sandbox=True`). Playwright's default
  `--no-sandbox` made the browser show an "unsupported command line flag" warning.
- Next ideas: turn deadlines into Work tasks, a weekly summary, run `sync` on a timer.

## Notes

- Scrape only your own account, slowly, and check the uni's terms for automated access.
- If login breaks, the page structure probably changed: re-probe the selectors above.

## Full crawl and auto-classification

- `crawl.py` reads everything the account can see through the site's REST API (course info, content
  trees, announcements, gradebook columns + own grades, groups, calendar) into `dump/` (raw JSON,
  ~9 MB, personal: `.gitignore` keeps it out of git, like `stream.json` / `stream.html`). `explore.py` / `probe_course.py` were used to find the endpoints.
- `load_crawl.py` puts it into `elearn.db`: `announcements` (all 704, classified) and `assessments`
  (94 gradebook columns with due date, weight major/minor, status, score).
- `classify.py` decides category + importance (1 act on it, 2 your week, 3 context, 4 optional,
  5 noise) from regexes over the title, then the first lines of the body. What it relies on:
  1. **Source**: a module's own page (`2701-CSC3034 ...`) is about that module; faculty / school /
     career / internship pages are broadcasts (mostly opportunities, events, notices).
  2. **Fixed phrasings**: `Due:`, `Grade posted:`, `Added:`, `New course available:`.
  3. **Assessment words** (test, quiz, assignment, project, proposal, exam, submission, deadline,
     extension, marks/scores/results released): importance 1.
  4. **Logistics words** (no class, replaced, venue, rescheduled, Week N, practical group, compulsory):
     importance 2.
  5. **Opportunity / event words** (internship, hackathon, career fair, workshop, webinar, invitation,
     survey): importance 4. **Notice words** (COVID, fraud, parking, ID card, iZone): 5.
  Gradebook columns: major (assignment, test, exam, project, report, proposal, mid-term, CA) vs minor
  (checkpoint quiz, pre-class, H5P, attendance, survey, activity).

## Outlook (email)

- `outlook.py` signs in to Outlook on the web (`outlook.office.com`, `<id>@imail.sunway.edu.my`, same password) with
  its own profile `~/.local/share/elearn/profile-outlook`. The first run needs `--show`: approve the number in Microsoft
  Authenticator ("Don't ask again for 14 days" is ticked, so later runs are headless; when it expires the Refresh logs
  "Outlook needs you to sign in": press **Sign in to Outlook** in Work → Settings, or run `outlook.py --show`).
- It reads the **whole inbox** (all pages, ~110 messages) through Outlook's own web API, using the access token the page already
  holds (read-only GETs), into `elearn.db` table `emails` (classified like announcements).

## The Announcements page (Work > Announcements)

- **Refresh** runs `refresh.py`: (1) eLearn activity stream, (2) eLearn announcements + gradebook of every course
  (`crawl.light` + `load_crawl.load`), (3) Outlook inbox. About a minute. Each step is logged in `sync_log`; the bar shows when each
  last ran, with a warning if it failed. `refresh.py stream|grades|mail` runs only some.
- All sources are merged into one list (`announcement_feed` in `plugins/app.py`): an email that repeats an announcement marks it
  "+ email" (same words in the title, same course, within 5 days; eLearn's own "New announcement" mails are matched through
  their preview), the same ad posted on several faculty pages shows once ("+N pages"), and messages only in Outlook appear
  in the right tier with an "email" badge.
- Your Work tasks are matched to eLearn assessments (same course + same due date or title words) and announcements/emails
  (`announcement_tasks`); matched items get a "task" badge, and eLearn deadlines that aren't a task get a "+ task" button.
- **Which courses are read:** the first step of `refresh.py` (`sync_courses`) switches off the modules you haven't starred in eLearn
  (`course_settings`, auto = 1) and deletes their rows from `stream_items`, `announcements` and `assessments`; star one later and it comes
  back at the next Refresh. Faculty / school / career pages are never switched off automatically. Double-clicking a chip switches a course by hand.
