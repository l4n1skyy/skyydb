import sys, json; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); import elearn, explore
from playwright.sync_api import sync_playwright
# Usage: probe_course.py <course id, like _12345_1>: saves what each endpoint answers for that course into dump/
if len(sys.argv) < 2: sys.exit("usage: probe_course.py <course id, like _12345_1>")
C = sys.argv[1]
paths=[f"/learn/api/public/v1/courses/{C}", f"/learn/api/public/v1/courses/{C}/contents?limit=200",
 f"/learn/api/public/v1/courses/{C}/announcements?limit=50", f"/learn/api/public/v1/courses/{C}/gradebook/columns?limit=100",
 f"/learn/api/public/v1/courses/{C}/gradebook/users/me", f"/learn/api/public/v1/courses/{C}/users/me",
 f"/learn/api/public/v1/courses/{C}/users?limit=5", f"/learn/api/public/v1/courses/{C}/groups?limit=20",
 f"/learn/api/v1/courses/{C}/contents?limit=200&expand=info", f"/learn/api/v1/courses/{C}/gradebook/grades?limit=100",
 f"/learn/api/v1/courses/{C}/announcements", f"/learn/api/v1/calendars/items?since=2026-01-01T00:00:00.000Z&until=2027-01-01T00:00:00.000Z&limit=200",
 f"/learn/api/v1/courses/{C}/gradebook/columns?limit=100", f"/learn/api/v1/users/me/courses/{C}/grades",
 f"/learn/api/public/v2/courses/{C}/contents?limit=200", f"/learn/api/public/v1/courses/{C}/resources?limit=50"]
with sync_playwright() as p:
    ctx=p.chromium.launch_persistent_context(elearn.PROFILE, executable_path=elearn.CHROMIUM, headless=True, chromium_sandbox=True)
    page=ctx.pages[0] if ctx.pages else ctx.new_page(); elearn.log_in(page)
    for path in paths:
        st,d=explore.api(page,path)
        n = len(d.get("results",[])) if isinstance(d,dict) and "results" in d else ""
        print(st,n,path.split("/learn/api/")[1][:75], (list(d)[:8] if isinstance(d,dict) else str(d)[:80]))
        explore.save("c_"+path.split("/learn/api/")[1].replace("/","_").replace("?","_")[:90]+".json", d)
    ctx.close()
