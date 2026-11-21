"""Everything the Announcements page shows, refreshed in one go (the Refresh button runs this):
  1. eLearn activity stream      (elearn.py: scrape_stream + save_items)
  2. eLearn announcements + gradebook (crawl.light + load_crawl.load), classified
  3. Outlook inbox               (outlook.py: Outlook's web API)
Each step is logged to sync_log; one failing doesn't stop the others.
Usage: python -I refresh.py [stream|grades|mail ...]   (no argument = all)"""
import sqlite3
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import crawl
import elearn
import load_crawl
import classify
import explore
import outlook
from playwright.sync_api import sync_playwright

DB = elearn.DB_DIR


def log(source, items=0, new=0, error=""):
    db = sqlite3.connect(DB / "elearn.db")
    db.executescript((DB / "schema" / "elearn.sql").read_text())
    db.execute("INSERT INTO sync_log (source, items, new, error) VALUES (?, ?, ?, ?)", (source, items, new, error[:300]))
    db.commit()
    db.close()


def off():
    """Courses switched off on the page: not read"""
    db = sqlite3.connect(DB / "elearn.db")
    db.executescript((DB / "schema" / "elearn.sql").read_text())
    names = {r[0] for r in db.execute("SELECT course FROM course_settings WHERE active = 0")}
    db.close()
    return names


def sync_courses(page):
    """Switch off (and remove the data of) the modules that aren't starred in eLearn; switch back on the ones that are, unless
    you turned them off by hand. Faculty / school / career pages are left alone. Returns (modules, switched off now)."""
    members = explore.api(page, "/learn/api/v1/users/me/memberships?limit=200&expand=course")[1]["results"]
    starred = {m["course"]["name"] for m in explore.api(page, "/learn/api/v1/users/me/memberships?favorite=true&limit=200&expand=course")[1]["results"]}
    modules = {m["course"]["name"] for m in members if classify.is_module(m["course"]["name"])}
    db = sqlite3.connect(DB / "elearn.db")
    db.executescript((DB / "schema" / "elearn.sql").read_text())
    newly = 0
    with db:
        for name in modules:
            row = db.execute("SELECT active, auto FROM course_settings WHERE course = ?", (name,)).fetchone()
            if name in starred:
                if row and row[1]:  # it was switched off by this step, and is starred now
                    db.execute("DELETE FROM course_settings WHERE course = ?", (name,))
            elif not row:
                db.execute("INSERT INTO course_settings (course, active, auto) VALUES (?, 0, 1)", (name,))
                newly += 1
        for name in (r[0] for r in db.execute("SELECT course FROM course_settings WHERE active = 0")):
            for table in ("stream_items", "announcements", "assessments"):
                db.execute(f"DELETE FROM {table} WHERE course = ?", (name,))
    db.close()
    return len(modules), newly


def step(source, fn):
    try:
        items, new = fn()
        log(source, items, new)
        print(f"{source}: {items} items, {new} new", flush=True)
    except Exception as err:  # noqa: BLE001 - one source failing must not stop the rest
        traceback.print_exc()
        log(source, error=str(err).splitlines()[0] if str(err) else type(err).__name__)


def main():
    want = set(sys.argv[1:]) or {"courses", "stream", "grades", "mail"}
    with sync_playwright() as p:
        if want & {"courses", "stream", "grades"}:
            ctx = p.chromium.launch_persistent_context(elearn.PROFILE, executable_path=elearn.CHROMIUM, headless=True, chromium_sandbox=True,
                                                       viewport={"width": 1400, "height": 900})
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                elearn.log_in(page)
                if "courses" in want:
                    step("courses", lambda: sync_courses(page))
                if "stream" in want:
                    step("stream", lambda: elearn.save_items(elearn.scrape_stream(page)))
                if "grades" in want:
                    def grades():
                        db = sqlite3.connect(DB / "elearn.db")
                        db.executescript((DB / "schema" / "elearn.sql").read_text())
                        n_ann, n_ass = load_crawl.load(crawl.light(page, skip=off()), db)
                        db.close()
                        return n_ann, n_ass
                    step("grades", grades)
            except Exception as err:  # noqa: BLE001
                traceback.print_exc()
                log("eLearn", error=f"login failed: {err}"[:300])
            finally:
                ctx.close()
        if "mail" in want:
            ctx = p.chromium.launch_persistent_context(str(outlook.PROFILE), executable_path="/usr/bin/chromium", headless=True,
                                                       chromium_sandbox=True, viewport={"width": 1400, "height": 900})
            try:
                user, pw = elearn.credentials()
                step("mail", lambda: outlook.save_mail(outlook.read_inbox(outlook.open_inbox(ctx, user, pw))))
            finally:
                ctx.close()


if __name__ == "__main__":
    main()
