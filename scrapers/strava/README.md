# strava

Signs in to strava.com as you (in Chromium, through Playwright) and reads **every activity you've
logged** into skyydb's `workouts.db` (table `strava_activities`). skyydb's Workouts → Stats shows
your running from it.

## Setup

| What | Where |
|---|---|
| Project | `scrapers/strava/` in skyydb (`strava.py`, `pyproject.toml`, `uv.lock`) |
| Python env | `~/.local/share/venvs/strava` (`playwright==1.65.0`), made with `uv` |
| Browser | the system `/usr/bin/chromium`, driven by Playwright (no browser download) |
| Browser profile | `~/.local/share/strava/profile`: Strava's cookies, so you stay signed in |
| Password | none stored: you sign in yourself in the window it opens |
| Signing in | an ordinary Chromium window (not driven by Playwright) on that profile, so Google sign-in works; Google refuses remote-controlled browsers ("This browser or app may not be secure"). It only watches the tab's address (Chromium's debugging port); once you're signed in it copies Strava's cookies into `~/.local/share/strava/cookies.json` (mode 600) and closes the window |
| Staying signed in | Strava's session cookie has no end date, so a closed browser forgets it: the copy gets 90 days and is put back into every run (and renewed after each one) |

Recreate it:

```bash
cd scrapers/strava   # in skyydb's folder
UV_PROJECT_ENVIRONMENT=~/.local/share/venvs/strava uv sync
```

## Usage

From skyydb: **Workouts → Settings** → paste your profile link → **Sign in to Strava**; later
**Sync now**, or just open Workouts → Stats (it syncs when the last read is over an hour old).
From a terminal:

```bash
PY=~/.local/share/venvs/strava/bin/python
$PY -I strava.py login        # Strava's sign-in in a normal Chromium window: sign in; it closes itself and reads everything
$PY -I strava.py sync         # what's new, plus the last three weeks again (edits, late uploads)
$PY -I strava.py sync --all   # every activity again; drops the ones you deleted on Strava
$PY -I strava.py probe        # saves what Strava answers into dump/ (when something changed)
```

`skyydb strava`, `skyydb strava all` and `skyydb strava login` run the same.

## How it reads them

- **Signed in?** It opens `strava.com/athlete/training` (My Activities). If Strava sends it to
  `/login`, the session has run out: sign in again.
- **The activities** come from the list that page loads as JSON, newest first, 20 at a time:
  `/athlete/training_activities?…&page=N&per_page=20` → `{models: [...], total}`. Each model has
  raw numbers next to the shown ones (`distance_raw` metres, `moving_time_raw` /
  `elapsed_time_raw` seconds, `elevation_gain_raw` metres, `start_time` local with offset). The
  whole model goes into `raw`.
- It waits a second between pages, and a normal sync stops three weeks before the newest
  activity it already has.
- Your profile's weekly bars (`/athletes/<id>/profile_intervals?interval=YYYYWW&interval_type=week`)
  show the same activities week by week; `probe` saves one to compare against.
- How each run went is written to `workouts.db`'s `settings` table (`strava_synced`,
  `strava_said`, `strava_error`, `strava_athlete`), which Workouts → Settings shows.

## Notes

- Scraping is against Strava's terms: it reads only your own account, slowly. Strava could change
  its pages at any time; if a sync says the list "didn't answer as before", run `probe` and look in
  `dump/` (personal: keep it out of git).
- `STRAVA_BASE`, `STRAVA_PROFILE` and `SKYYDB_DIR` point it at a test server, profile and skyydb
  copy instead of the real ones.
