"""Load dump/crawl.json (from crawl.py) into skyydb's elearn.db: every announcement, classified,
and every gradebook column with your own result. Safe to run again."""
import json, os, re, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import classify

DB = Path(os.environ.get("SKYYDB_DIR") or Path(__file__).resolve().parents[2])  # skyydb's folder: this is scrapers/<name>/ in it
HERE = Path(__file__).parent

def local(iso):
    """'2026-11-15T15:59:00.000Z' (UTC) -> '2026-11-15 23:59' (Malaysia, UTC+8)"""
    if not iso:
        return ""
    return (datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M") + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")

def load(crawl, conn):
    """Put a crawl ({"courses": [...]}) into announcements + assessments. Returns (announcements, assessments)."""
    n_ann = n_ass = 0
    with conn:
        for c in crawl["courses"]:
            module = classify.is_module(c["name"])
            for a in c["announcements"]:
                body = classify.text(a.get("body"))  # the whole post, paragraphs kept
                cat, imp = classify.classify(a["title"], body, c["name"])
                conn.execute("""INSERT INTO announcements (id, course_id, course, module, title, body, posted_at, category, importance)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET title = excluded.title, body = excluded.body, category = excluded.category,
                                                              importance = excluded.importance, module = excluded.module,
                                    -- a post the activity stream saw first may have been stored without a date: take the crawl's
                                    posted_at = CASE WHEN announcements.posted_at = '' THEN excluded.posted_at ELSE announcements.posted_at END""",
                             (a["id"], c["id"], c["name"], int(module), a["title"].strip(), body, local(a.get("created")), cat, imp))
                n_ann += 1
            mine = {g["columnId"]: g for g in c["my_grades"]}
            for col in c["gradebook_columns"]:
                g = mine.get(col["id"], {})
                grading = col.get("grading") or {}
                conn.execute("""INSERT INTO assessments (id, course_id, course, name, weight, possible, due, status, score)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET name = excluded.name, weight = excluded.weight, possible = excluded.possible,
                                                              due = excluded.due, status = excluded.status, score = excluded.score""",
                             (col["id"], c["id"], c["name"], col["name"].strip(), classify.column_weight(col["name"]),
                              (col.get("score") or {}).get("possible"), local(grading.get("due")), g.get("status") or "", g.get("score")))
                n_ass += 1
    return n_ann, n_ass


def main():
    crawl = json.loads((HERE / "dump" / "crawl.json").read_text())
    conn = sqlite3.connect(DB / "elearn.db")
    conn.executescript((DB / "schema" / "elearn.sql").read_text())
    print("loaded %d announcements, %d assessments" % load(crawl, conn))

if __name__ == "__main__":
    main()
