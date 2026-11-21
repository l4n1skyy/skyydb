"""Strava: read every activity you've logged, through strava.com itself, signed in as you.

    strava.py login          open Strava's sign-in in an ordinary Chromium window: sign in (any way,
                             Google too); the window closes by itself and every activity is read
    strava.py sync           read what's new (and the latest weeks again) into skyydb's workouts.db
    strava.py sync --all     read every activity again, and drop the ones deleted on Strava
    strava.py probe          save what Strava's pages answer into dump/ (when something changes)
    add --show to watch the browser (login always shows it)

Where the activities come from: the "My Activities" list (strava.com/athlete/training) loads
them page by page as JSON while you're signed in; that list has every activity, private ones
included. The weekly bars on your profile (/athletes/<id>/profile_intervals) are read by
probe only, to check against.

The browser profile (Strava's cookies, so you stay signed in) is ~/.local/share/strava/profile.
No password is stored anywhere. It reads slowly (a pause between pages) and only your account.
"""
import json
import os
import socket
import sqlite3
import subprocess
import urllib.request
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# (The environment variables are for trying it against a pretend Strava and a copy of skyydb;
# STRAVA_LOGIN_PORT / STRAVA_LOGIN_FLAGS too)
BASE = os.environ.get("STRAVA_BASE", "https://www.strava.com")
HERE = Path(__file__).parent
PROFILE = Path(os.environ.get("STRAVA_PROFILE", Path.home() / ".local/share/strava/profile"))
COOKIES = PROFILE.parent / "cookies.json"  # Strava's cookies from signing in (mode 600), put back into every run
CHROMIUM = "/usr/bin/chromium"
DB_DIR = Path(os.environ.get("SKYYDB_DIR") or Path(__file__).resolve().parents[2])  # skyydb's folder: this is scrapers/<name>/ in it
PER_PAGE = 20  # what the My Activities page itself asks for
PAUSE = 1.0  # seconds between pages, to go easy on Strava
RECHECK = timedelta(days=21)  # a normal sync reads back this far (edits, late uploads)
LIST = (f"{BASE}/athlete/training_activities?keywords=&activity_type=&workout_type=&commute=&private_activities="
        f"&trainer=&gear=&new_activity_only=false&order=start_date_local+DESC&page={{page}}&per_page={PER_PAGE}")


# --- skyydb's database -------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_DIR / "workouts.db", timeout=30)
    conn.row_factory = sqlite3.Row
    # The tables come from skyydb's schema (it creates what's missing)
    conn.executescript((DB_DIR / "schema" / "workouts.sql").read_text())
    return conn


def note(**values):
    """How the run went, for Workouts > Settings (strava_synced, strava_said, strava_error, ...)"""
    with db() as conn:
        conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                         [(k, "" if v is None else str(v)) for k, v in values.items()])


def setting(key):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", [key]).fetchone()
    return row[0] if row else ""


# --- the browser -----------------------------------------------------------------------

def browser(p, show):
    PROFILE.mkdir(parents=True, exist_ok=True)
    ctx = p.chromium.launch_persistent_context(
        str(PROFILE), executable_path=CHROMIUM, headless=not show, chromium_sandbox=True,
        viewport={"width": 1280, "height": 900}, locale="en-GB")
    if COOKIES.exists():  # the sign-in, even the part a closed browser drops
        try:
            ctx.add_cookies(json.loads(COOKIES.read_text()))
        except Exception as e:
            print(f"strava: couldn't put the saved cookies back ({e})", file=sys.stderr)
    return ctx


def save_cookies(cookies):
    """Keep Strava's cookies for the next runs. Its session cookie has no end date (a browser
    forgets it on closing), so it's given one: 90 days; Strava still decides when it's over."""
    keep = []
    for c in cookies:
        if "strava.com" not in c.get("domain", "") and not c.get("domain", "").endswith(BASE.split("//")[-1].split(":")[0]):
            continue
        c = dict(c)
        if c.get("expires", -1) in (-1, None) or c["expires"] < time.time():
            c["expires"] = int(time.time()) + 90 * 86400
        keep.append(c)
    COOKIES.parent.mkdir(parents=True, exist_ok=True)
    COOKIES.write_text(json.dumps(keep))
    COOKIES.chmod(0o600)


def signed_in_url(url):
    return url.startswith(BASE) and not any(x in url for x in ("/login", "/session", "/register", "/onboarding", "/oauth"))


def signed_in(page):
    return signed_in_url(page.url)


def open_signed_in(page):
    """Strava's My Activities page, or False when Strava wants you to sign in"""
    page.goto(f"{BASE}/athlete/training", wait_until="domcontentloaded")
    return signed_in(page)


def get(page, url):
    """GET a Strava address from inside its own page (its cookies, its headers): (status, text)"""
    r = page.evaluate("""async (url) => {
        const res = await fetch(url, {credentials: "same-origin", headers: {
            "X-Requested-With": "XMLHttpRequest", "Accept": "text/javascript, application/json, */*; q=0.01"}});
        return [res.status, await res.text()];
    }""", url)
    return r[0], r[1]


def athlete_id(page):
    """The signed-in athlete's id, from a link to their own profile"""
    try:
        return page.evaluate("""() => {
            const own = document.querySelector('a[href*="/athletes/"][class*="nav"], .user-menu a[href*="/athletes/"], a[href^="/athletes/"]');
            const m = own && own.getAttribute("href").match(/athletes\\/(\\d+)/);
            return m ? m[1] : (window.currentAthlete && String(window.currentAthlete.id)) || "";
        }""") or ""
    except Exception:
        return ""


# --- one activity ------------------------------------------------------------------------

def number(value):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def local_start(m):
    """'2026-11-08T07:12:33+0800' -> '2026-11-08 07:12:33' (the time where you were)"""
    if m.get("start_time"):
        return str(m["start_time"])[:19].replace("T", " ")
    if m.get("start_date_local_raw"):
        return datetime.fromtimestamp(int(m["start_date_local_raw"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return ""


def row(m):
    """A My Activities entry as a strava_activities row. The list gives raw numbers (metres,
    seconds) next to the shown ones; if a raw one is missing, the shown one is converted."""
    distance = number(m.get("distance_raw"))
    if distance is None and number(m.get("distance")) is not None:
        distance = number(m["distance"]) * (1609.344 if str(m.get("short_unit", "km")).startswith("mi") else 1000)
    elevation = number(m.get("elevation_gain_raw"))
    if elevation is None and number(m.get("elevation_gain")) is not None:
        elevation = number(m["elevation_gain"]) * (0.3048 if str(m.get("elevation_unit", "m")).startswith("ft") else 1)
    sport = m.get("sport_type") or m.get("type") or m.get("display_type") or m.get("activity_type_display_name") or ""
    return (int(m["id"]), m.get("name") or "", sport, local_start(m), distance or 0,
            int(number(m.get("moving_time_raw")) or 0), int(number(m.get("elapsed_time_raw")) or 0),
            elevation or 0, number(m.get("average_heartrate")), number(m.get("max_heartrate")),
            json.dumps(m, separators=(",", ":")))


# --- sync ----------------------------------------------------------------------------------

def read_list(page, everything):
    """Every activity on My Activities (or, normally, down to RECHECK before the newest one already
    here). Newest first, a page at a time."""
    with db() as conn:
        known = {r[0]: r[1] for r in conn.execute("SELECT id, start FROM strava_activities")}
    newest = max(known.values(), default="")
    stop_before = (datetime.fromisoformat(newest) - RECHECK).strftime("%Y-%m-%d %H:%M:%S") if newest and not everything else ""
    got, page_no, total = [], 1, None
    while True:
        status, text = get(page, LIST.format(page=page_no))
        if status in (401, 403) or "/login" in text[:2000]:
            raise RuntimeError("Strava wants you to sign in again: press Sign in to Strava (or run: skyydb strava login)")
        if status != 200:
            raise RuntimeError(f"Strava's activity list answered {status}")
        try:
            data = json.loads(text)
        except ValueError:
            raise RuntimeError("Strava's activity list didn't answer as before (run: strava.py probe)") from None
        models = data.get("models") or []
        total = data.get("total", total)
        got += models
        print(f"page {page_no}: {len(models)} activities ({len(got)}{f' of {total}' if total else ''})", flush=True)
        oldest = min((local_start(m) for m in models), default="")
        if not models or (total and len(got) >= total) or (stop_before and oldest and oldest < stop_before):
            break
        page_no += 1
        time.sleep(PAUSE)
    return got, known


def save(models, known, everything):
    rows = [row(m) for m in models if m.get("id")]
    with db() as conn:
        conn.executemany("""INSERT INTO strava_activities (id, name, sport, start, distance, moving_time, elapsed_time,
                                                           elevation, avg_hr, max_hr, raw)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (id) DO UPDATE SET name = excluded.name, sport = excluded.sport, start = excluded.start,
                              distance = excluded.distance, moving_time = excluded.moving_time, elapsed_time = excluded.elapsed_time,
                              elevation = excluded.elevation,
                              avg_hr = coalesce(excluded.avg_hr, strava_activities.avg_hr),
                              max_hr = coalesce(excluded.max_hr, strava_activities.max_hr), raw = excluded.raw""", rows)
        dropped = 0
        if everything and rows:  # gone from Strava: gone here too
            ids = [r[0] for r in rows]
            dropped = conn.execute(f"DELETE FROM strava_activities WHERE id NOT IN ({','.join('?' * len(ids))})", ids).rowcount
    new = sum(r[0] not in known for r in rows)
    return new, len(rows) - new, dropped


def sync(show=False, everything=False, page=None):
    started = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        if page is None:
            with sync_playwright() as p:
                ctx = browser(p, show)
                try:
                    return sync(show, everything, ctx.pages[0] if ctx.pages else ctx.new_page())
                finally:
                    ctx.close()
        if not open_signed_in(page):
            raise RuntimeError("Strava wants you to sign in: press Sign in to Strava (or run: skyydb strava login)")
        me = athlete_id(page)
        save_cookies(page.context.cookies())  # Strava may have renewed them
        models, known = read_list(page, everything)
        new, again, dropped = save(models, known, everything)
        said = (f"{new} new activit{'y' if new == 1 else 'ies'}" + (f", {again} read again" if again else "")
                + (f", {dropped} deleted on Strava dropped" if dropped else ""))
        note(strava_synced=started, strava_said=said, strava_error="", **({"strava_athlete": me} if me else {}))
        print(said)
        return True
    except Exception as e:  # what went wrong goes on the page
        note(strava_synced=started, strava_error=str(e)[:300])
        print(f"strava: {e}", file=sys.stderr)
        return False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def login():
    """Strava's sign-in in an ordinary Chromium window on the scraper's profile. Nothing drives it
    while you sign in, so Google's and Apple's sign-in work (they turn away remote-controlled
    browsers: "This browser or app may not be secure"). It only watches which address the tab is
    on; once you're on Strava signed in, it copies Strava's cookies (the session ones too, which a
    closed browser forgets: that's why it kept asking to sign in again), closes the window, and
    reads every activity."""
    PROFILE.mkdir(parents=True, exist_ok=True)
    note(strava_error="")
    port = int(os.environ.get("STRAVA_LOGIN_PORT") or free_port())
    extra = os.environ.get("STRAVA_LOGIN_FLAGS", "").split()  # (for trying it out without a window: --headless=new)
    window = subprocess.Popen([CHROMIUM, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={port}", "--no-first-run",
                               "--no-default-browser-check", *extra, "--new-window", f"{BASE}/login"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline, done = time.time() + 15 * 60, False
    try:
        while not done and window.poll() is None and time.time() < deadline:
            time.sleep(2)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
                    tabs = json.loads(r.read())
            except (OSError, ValueError):
                continue
            done = any(t.get("type") == "page" and signed_in_url(t.get("url", "")) for t in tabs)
        if not done:
            note(strava_error="The Strava window was closed (or 15 minutes went by) before you were signed in")
            return False
        time.sleep(2)  # let Strava finish setting its cookies
        with sync_playwright() as p:
            remote = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            save_cookies(remote.contexts[0].cookies())
            remote.close()
    finally:
        if window.poll() is None:
            window.terminate()
            try:
                window.wait(timeout=10)
            except subprocess.TimeoutExpired:
                window.kill()
    print("signed in; reading your activities", flush=True)
    return sync(everything=True)


def probe(show=False):
    """Save the pages it reads (and the profile's weekly bars) into dump/, to see what Strava answers now"""
    out = HERE / "dump"
    out.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = browser(p, show)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            ok = open_signed_in(page)
            (out / "training.html").write_text(page.content())
            print("signed in:", ok, page.url)
            if not ok:
                return False
            status, text = get(page, LIST.format(page=1))
            (out / "training_activities.json").write_text(text)
            print("activity list:", status, len(text))
            me = setting("strava_profile") or athlete_id(page)
            week = datetime.now().strftime("%G%V")
            status, text = get(page, f"{BASE}/athletes/{me}/profile_intervals?interval={week}&interval_type=week&chart_type=miles&year_offset=0")
            (out / "profile_intervals.txt").write_text(text)
            print("profile weekly bars:", status, len(text), "athlete", me)
            return True
        finally:
            ctx.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    show = "--show" in args
    cmd = next((a for a in args if not a.startswith("--")), "sync")
    if cmd == "login":
        sys.exit(0 if login() else 1)
    if cmd == "sync":
        sys.exit(0 if sync(show, everything="--all" in args) else 1)
    if cmd == "probe":
        sys.exit(0 if probe(show) else 1)
    print(__doc__)
    sys.exit(2)
