"""Read-only crawl of everything my own eLearn account can see, via the same REST API the
site uses. One request at a time with a pause. Writes dump/crawl.json (+ one file per course)."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); import elearn, explore
from playwright.sync_api import sync_playwright

PUB = "/learn/api/public/v1"
OUT = Path(__file__).parent / "dump"

def pages(page, path):
    """All results of a paged list endpoint"""
    out, url = [], path + ("&" if "?" in path else "?") + "limit=200"
    for _ in range(20):
        st, d = explore.api(page, url)
        if st != 200 or not isinstance(d, dict):
            return out, st
        out += d.get("results", [])
        nxt = (d.get("paging") or {}).get("nextPage")
        if not nxt:
            return out, 200
        url = nxt
    return out, 200

def tree(page, cid, parent=None, depth=0):
    path = f"{PUB}/courses/{cid}/contents" + (f"/{parent}/children" if parent else "")
    items, st = pages(page, path)
    for it in items:
        if it.get("hasChildren") and depth < 8:
            it["children"] = tree(page, cid, it["id"], depth + 1)
        h = (it.get("contentHandler") or {}).get("id", "")
        if h in ("resource/x-bb-file", "resource/x-bb-document") or it.get("hasAssociatedGroups") is None:
            st, att = explore.api(page, f"{PUB}/courses/{cid}/contents/{it['id']}/attachments")
            if st == 200 and isinstance(att, dict) and att.get("results"):
                it["attachments"] = att["results"]
    return items

def light(page, skip=()):
    """Just what the announcements page needs: each course's announcements, gradebook columns and my grades"""
    me = explore.api(page, f"{PUB}/users/me")[1]
    members, _ = pages(page, "/learn/api/v1/users/me/memberships?expand=course.effectiveAvailability")
    courses = []
    for m in members:
        cid, name = m["courseId"], (m.get("course") or {}).get("name")
        if name in skip:
            continue
        ann, _ = pages(page, f"{PUB}/courses/{cid}/announcements")
        cols, _ = pages(page, f"{PUB}/courses/{cid}/gradebook/columns")
        grades = []
        for col in cols:
            st, g = explore.api(page, f"{PUB}/courses/{cid}/gradebook/columns/{col['id']}/users/{me['id']}")
            if st == 200:
                grades.append(g)
        courses.append({"id": cid, "name": name, "announcements": ann, "gradebook_columns": cols, "my_grades": grades})
    return {"courses": courses}


if __name__ == "__main__":
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(elearn.PROFILE, executable_path=elearn.CHROMIUM, headless=True, chromium_sandbox=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        elearn.log_in(page)
        me = explore.api(page, f"{PUB}/users/me")[1]
        members, _ = pages(page, "/learn/api/v1/users/me/memberships?expand=course.effectiveAvailability")
        result = {"crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"), "courses": []}
        (OUT / "courses").mkdir(exist_ok=True)
        for m in members:
            c = m.get("course") or {}
            cid = m["courseId"]
            print("course", cid, c.get("name"), flush=True)
            info = {"id": cid, "name": c.get("name"), "code": c.get("courseId"), "ultra": c.get("ultraStatus"), "organization": c.get("isOrganization"),
                    "membership": {k: m.get(k) for k in ("lastAccessDate", "created", "availability")}}
            info["course"] = explore.api(page, f"{PUB}/courses/{cid}")[1]
            info["announcements"], _ = pages(page, f"{PUB}/courses/{cid}/announcements")
            info["contents"] = tree(page, cid)
            cols, st = pages(page, f"{PUB}/courses/{cid}/gradebook/columns")
            info["gradebook_columns"] = cols
            info["my_grades"] = []
            for col in cols:
                st, g = explore.api(page, f"{PUB}/courses/{cid}/gradebook/columns/{col['id']}/users/{me['id']}")
                if st == 200:
                    info["my_grades"].append(g)
            info["groups"], _ = pages(page, f"{PUB}/courses/{cid}/groups")
            (OUT / "courses" / f"{cid}.json").write_text(json.dumps(info, indent=1, ensure_ascii=False))
            result["courses"].append(info)
        result["calendar"], _ = pages(page, f"{PUB}/calendars/items")
        result["calendars"] = explore.api(page, f"{PUB}/calendars")[1]
        (OUT / "crawl.json").write_text(json.dumps(result, ensure_ascii=False))
        print("done", len(result["courses"]), "courses")
        ctx.close()
