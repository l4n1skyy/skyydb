"""Read-only exploration of what eLearn (Blackboard Learn Ultra) exposes to my own account.
Calls the same REST endpoints the web app itself uses, from inside the logged-in browser
page, one at a time with a short pause. Raw answers go to dump/."""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent)); import elearn

OUT = Path(__file__).parent / "dump"

def api(page, path):
    r = page.context.request.get(elearn.BASE + path, headers={"Accept": "application/json"})
    time.sleep(0.4)
    try:
        return r.status, r.json()
    except Exception:
        return r.status, r.text()[:200]

def save(name, data):
    name = "".join(ch if ch.isalnum() or ch in "._-=" else "_" for ch in name)[:100]
    (OUT / name).write_text(json.dumps(data, indent=1, ensure_ascii=False))

if __name__ == "__main__":
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(elearn.PROFILE, executable_path=elearn.CHROMIUM, headless=True, chromium_sandbox=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        elearn.log_in(page)
        for path in ["/learn/api/public/v1/users/me", "/learn/api/public/v1/users/me/courses?limit=100&expand=course",
                     "/learn/api/public/v1/courses?limit=5", "/learn/api/public/v1/calendars/items?limit=5",
                     "/learn/api/v1/users/me", "/learn/api/v1/users/me/memberships?expand=course.effectiveAvailability,course.permissions&includeCount=true&limit=100",
                     "/learn/api/v1/streams/ultra"]:
            st, d = api(page, path)
            print(st, path, (list(d)[:12] if isinstance(d, dict) else str(d)[:100]))
            save("probe_" + path.strip("/").replace("/", "_").replace("?", "_")[:80] + ".json", d)
        ctx.close()
