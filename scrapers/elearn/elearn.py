"""Sunway eLearn (Blackboard Ultra): log in and scrape the activity stream.

    elearn.py login            open the site and log in (browser stays open for you)
    elearn.py manual           open the Sunway ID login and let YOU type the credentials
    elearn.py sync             scrape, then add the new items to skyydb's elearn.db
    elearn.py stream           scrape /ultra/stream into stream.json (+ stream.html for debugging)
    add --show to watch the browser; login always shows it.

Credentials: ~/.config/elearn/credentials (ELEARN_USER=..., ELEARN_PASS=...), outside this folder.
The browser profile (cookies, so you stay logged in) lives in ~/.local/share/elearn/profile.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import classify  # noqa: E402
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = "https://elearn.sunway.edu.my"
STREAM = f"{BASE}/ultra/stream"
HERE = Path(__file__).parent
PROFILE = Path.home() / ".local/share/elearn/profile"
CREDS = Path.home() / ".config/elearn/credentials"
CHROMIUM = "/usr/bin/chromium"
DB_DIR = Path(os.environ.get("SKYYDB_DIR") or Path(__file__).resolve().parents[2])  # skyydb's folder: this is scrapers/<name>/ in it


def credentials():
    env = dict(line.split("=", 1) for line in CREDS.read_text().splitlines() if "=" in line)
    return env["ELEARN_USER"], env["ELEARN_PASS"]


def logged_in(page):
    return "/ultra/" in page.url and "login" not in page.url


def accept_privacy(page):
    """The 'Privacy, cookies and terms of use' dialog covers the login page until accepted."""
    btn = page.locator("#agree_button")
    try:
        btn.wait_for(state="visible", timeout=4000)
        btn.click()
    except PWTimeout:
        pass


def wait_until_closed(page):
    """Keep the browser open until you close its window (works without a terminal)."""
    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass


def manual_login(page):
    """Open the Sunway ID form and wait while you sign in yourself (nothing is filled in)."""
    page.goto(STREAM, wait_until="domcontentloaded")
    for _ in range(40):
        page.wait_for_timeout(500)
        if page.locator("#userNameInput").count():
            break
        if page.locator("#agree_button").is_visible():
            page.click("#agree_button")
        elif page.get_by_role("button", name="Sunway ID").is_visible():
            page.get_by_role("button", name="Sunway ID").click()
    print("Type your credentials in the browser window...", file=sys.stderr)
    page.wait_for_url(re.compile(r"elearn\.sunway\.edu\.my/ultra/"), timeout=600000)


def log_in(page):
    """Go to the stream. If bounced to a login page: accept the privacy dialog, choose
    'Sunway ID', fill the ADFS form (pinwheel.sunway.edu.my) and sign in."""
    page.goto(STREAM, wait_until="domcontentloaded")
    for _ in range(40):  # settle: either the stream, the Blackboard login, or ADFS
        page.wait_for_timeout(500)
        if "/ultra/stream" in page.url and "new_loc" not in page.url:
            return
        if page.locator("#userNameInput").count():
            break
        if page.locator("#agree_button").is_visible():
            page.click("#agree_button")
        elif page.get_by_role("button", name="Sunway ID").is_visible():
            page.get_by_role("button", name="Sunway ID").click()
    else:
        raise RuntimeError(f"stuck on {page.url}")
    user, password = credentials()
    page.fill("#userNameInput", user)
    page.fill("#passwordInput", password)
    page.click("#submitButton")
    if page.locator("#passwordInput").count() and "error" in (page.locator("#errorText").text_content() or "").lower():
        raise RuntimeError("login rejected")
    print("signed in; waiting for the stream (enter an MFA code in the window if asked)", file=sys.stderr)
    page.wait_for_url(re.compile(r"elearn\.sunway\.edu\.my/ultra/"), timeout=180000)


# Runs in the page: each group heading (Upcoming / Today / Recent) with its items.
EXTRACT_JS = """() => {
  const t = (el, sel) => { const e = el.querySelector(sel); return e ? e.textContent.replace(/\\s+/g, ' ').trim() : ''; };
  const out = [];
  document.querySelectorAll('.activity-group').forEach(g => {
    const group = t(g, '.activity-group-title');
    g.querySelectorAll('li.stream-item-container').forEach(li => {
      const a = li.querySelector('a.js-title-link');
      out.push({
        group,
        date: t(li, '.timestamp .date'),
        time: t(li, '.timestamp .time'),
        course: t(li, '.context'),
        title: t(li, '.name'),
        content: t(li, '.content'),
        due: t(li, '.due-date'),
        link: a ? a.href : '',
      });
    });
  });
  return out;
}"""


def scrape_stream(page):
    page.goto(STREAM, wait_until="domcontentloaded")
    page.wait_for_selector("[data-test-id='stream-item'], .activity-stream, #main-content", timeout=60000)
    page.wait_for_load_state("networkidle")
    # Load older items by scrolling the stream until nothing new appears.
    last = -1
    for _ in range(15):
        n = page.locator("[id^='stream-item'], .stream-item, bb-stream-item").count()
        if n == last:
            break
        last = n
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1200)
    (HERE / "stream.html").write_text(page.content())
    items = page.evaluate(EXTRACT_JS)
    out = {"scraped_at": datetime.now().isoformat(timespec="seconds"), "items": items}
    (HERE / "stream.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return items


# --- Into skyydb's elearn.db --------------------------------------------------------

def parse_time(text):
    try:
        return datetime.strptime(text.strip(), "%I:%M %p").time()
    except ValueError:
        return None


def posted_at(date_text, time_text, now):
    """'Oct 26, 2026' + '6:08 PM', or a relative '5 hours' + '9:21 AM', as 'YYYY-MM-DD HH:MM'"""
    clock = parse_time(time_text)
    rel = re.match(r"(\d+)\s*(minute|hour|day)", date_text)
    if rel:
        ago = now - timedelta(**{rel.group(2) + "s": int(rel.group(1))})
        if not clock:
            return ago.strftime("%Y-%m-%d %H:%M")
        at = datetime.combine(ago.date(), clock)
        if at > now:
            at -= timedelta(days=1)
        elif ago - at > timedelta(hours=12):
            at += timedelta(days=1)
        return at.strftime("%Y-%m-%d %H:%M")
    try:
        day = datetime.strptime(date_text.strip(), "%b %d, %Y").date()
    except ValueError:
        return ""
    return datetime.combine(day, clock or datetime.min.time()).strftime("%Y-%m-%d %H:%M")


def due_at(text):
    """'Due Date: 11/15/26, 11:59 PM (UTC+8)' as 'YYYY-MM-DD HH:MM'"""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4}),?\s*(\d{1,2}:\d{2}\s*[AP]M)?", text)
    if not m:
        return ""
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    clock = parse_time(m.group(4) or "") or datetime.min.time()
    try:
        return datetime(year + 2000 if year < 100 else year, month, day, clock.hour, clock.minute).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def kind_of(item):
    title, link = item["title"], item["link"]
    if title.startswith("Due:") or item["due"]:
        return "deadline"
    if title.startswith("Grade posted"):
        return "grade"
    if title.startswith(("New course available", "New organization available")):
        return "enrolment"
    if "announcement" in link or item["course"] == "Announcement":
        return "announcement"
    if title.startswith("Added:"):
        return "content"
    return "other"


def save_items(items):
    """Add the new items to skyydb's elearn.db, refresh the ones already there. Returns (seen, new)."""
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DB_DIR / "elearn.db")
    conn.executescript((DB_DIR / "schema" / "elearn.sql").read_text())
    off = {r[0] for r in conn.execute("SELECT course FROM course_settings WHERE active = 0")}  # switched off on the page
    items = [i for i in items if i.get("course") not in off]
    new = 0
    with conn:
        for it in items:
            posted = posted_at(it["date"], it["time"], now)
            link = "" if it["link"].startswith("javascript:") else it["link"]
            due = due_at(it["due"]) if it["due"] else ""
            key = hashlib.sha1("|".join([it["course"], it["title"], it["content"], link, posted]).encode()).hexdigest()
            score = re.search(r"([\d.]+)\s*/\s*([\d.]+)", it["content"]) if it["title"].startswith("Grade posted") else None
            row = (key, posted, kind_of(it), it["course"], it["title"], it["content"], due, link, it["group"], stamp, stamp, f"{score.group(1)} / {score.group(2)}" if score else "")
            ann = re.search(r"courseId=(_\d+_1)&announcementId=(_\d+_1)", link)
            if ann and kind_of(it) == "announcement":  # keep the announcements table current between full crawls
                cat, imp = classify.classify(it["title"], it["content"], it["course"])
                conn.execute("""INSERT OR IGNORE INTO announcements (id, course_id, course, module, title, body, posted_at, category, importance)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (ann.group(2), ann.group(1), it["course"], int(classify.is_module(it["course"])),
                                                                          it["title"], it["content"], posted, cat, imp))
            done = conn.execute("UPDATE stream_items SET grp = ?, last_seen = ? WHERE key = ?", (it["group"], stamp, key))
            if done.rowcount == 0:
                conn.execute("""INSERT INTO stream_items (key, posted_at, kind, course, title, content, due, link, grp, first_seen, last_seen, score)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
                new += 1
        conn.execute("INSERT INTO scrapes (at, items, new) VALUES (?, ?, ?)", (stamp, len(items), new))
    conn.close()
    return len(items), new


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stream"
    show = "--show" in sys.argv or cmd in ("login", "manual")
    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, executable_path=CHROMIUM, headless=not show, chromium_sandbox=True, viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if cmd == "manual":
            manual_login(page)
            print("Logged in by hand:", page.url)
            page.wait_for_timeout(5000)  # let the session cookies settle into the profile
            wait_until_closed(page)
            ctx.close()
            return
        log_in(page)
        if cmd == "login":
            print("Logged in:", page.url)
            wait_until_closed(page)
        elif cmd == "sync":
            seen, new = save_items(scrape_stream(page))
            print(f"synced {seen} items, {new} new")
        elif cmd == "stream":
            items = scrape_stream(page)
            print(f"saved stream.html and stream.json ({len(items)} items)")
        ctx.close()


if __name__ == "__main__":
    main()
