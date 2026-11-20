-- eLearn: what the Sunway eLearn (Blackboard) activity stream shows, scraped by
-- ~/Shared/03_AREAS/_AUTOMATION/elearn (`elearn.py sync`). One row per stream item;
-- a scrape only adds new items and refreshes the ones it sees again, so nothing is lost
-- when an item scrolls out of the stream.
-- `skyydb` applies this file every time it runs, so it only creates what's missing; views are rebuilt.

CREATE TABLE IF NOT EXISTS stream_items (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,            -- hash of course + title + content + posted time, to recognise an item again
    posted_at  TEXT NOT NULL DEFAULT '',        -- 'YYYY-MM-DD HH:MM' (the stream shows "5 hours" for recent ones; worked out from the scrape time)
    kind       TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('deadline', 'grade', 'announcement', 'content', 'enrolment', 'other')),
    course     TEXT NOT NULL DEFAULT '',        -- as eLearn names it, e.g. 2701-CSC3034 COMPUTATIONAL INTELLIGENCE
    title      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    due        TEXT NOT NULL DEFAULT '',        -- 'YYYY-MM-DD HH:MM' for deadlines
    score      TEXT NOT NULL DEFAULT '',        -- grades: '22 / 25'
    link       TEXT NOT NULL DEFAULT '',
    grp        TEXT NOT NULL DEFAULT '',        -- the stream's own heading when last seen: Upcoming / Today / Recent
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0 CHECK (read IN (0, 1))
);
CREATE INDEX IF NOT EXISTS stream_items_posted ON stream_items (posted_at);
CREATE INDEX IF NOT EXISTS stream_items_due ON stream_items (due);

CREATE TABLE IF NOT EXISTS scrapes (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL,
    items      INTEGER NOT NULL DEFAULT 0,      -- seen in the stream
    new        INTEGER NOT NULL DEFAULT 0,
    error      TEXT NOT NULL DEFAULT ''
);

-- From the full crawl (elearn project, `load_crawl.py`): every announcement a course or
-- organisation page ever posted, classified by classify.py (category + importance 1-5)
CREATE TABLE IF NOT EXISTS announcements (
    id         TEXT PRIMARY KEY,
    course_id  TEXT NOT NULL,
    course     TEXT NOT NULL DEFAULT '',
    module     INTEGER NOT NULL DEFAULT 0 CHECK (module IN (0, 1)),   -- 1 = a module's own page, 0 = faculty / school / career / internship page
    title      TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    posted_at  TEXT NOT NULL DEFAULT '',        -- 'YYYY-MM-DD HH:MM' local time
    category   TEXT NOT NULL DEFAULT '',        -- grade, deadline, exam, assessment, class, team, welcome, info, event, opportunity, notice, general
    importance INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5)
);
CREATE INDEX IF NOT EXISTS announcements_posted ON announcements (posted_at);

-- Gradebook columns with your own result: what is assessed, when, how big a piece it is
CREATE TABLE IF NOT EXISTS assessments (
    id        TEXT PRIMARY KEY,                 -- gradebook column id
    course_id TEXT NOT NULL,
    course    TEXT NOT NULL DEFAULT '',
    name      TEXT NOT NULL DEFAULT '',
    weight    TEXT NOT NULL DEFAULT 'minor' CHECK (weight IN ('major', 'minor')),   -- test / exam / assignment / project vs quiz / attendance / pre-class
    possible  REAL,
    due       TEXT NOT NULL DEFAULT '',         -- 'YYYY-MM-DD HH:MM' local time
    status    TEXT NOT NULL DEFAULT '',         -- Graded / NeedsGrading / ... ('' = nothing yet)
    score     REAL
);

-- Outlook inbox (read by outlook.py through Outlook's own web API): one row per message
CREATE TABLE IF NOT EXISTS emails (
    id         TEXT PRIMARY KEY,
    conv       TEXT NOT NULL DEFAULT '',      -- conversation id
    sender     TEXT NOT NULL DEFAULT '',
    addr       TEXT NOT NULL DEFAULT '',
    subject    TEXT NOT NULL DEFAULT '',
    preview    TEXT NOT NULL DEFAULT '',
    received   TEXT NOT NULL DEFAULT '',      -- 'YYYY-MM-DD HH:MM' local time
    link       TEXT NOT NULL DEFAULT '',
    is_read    INTEGER NOT NULL DEFAULT 1,
    category   TEXT NOT NULL DEFAULT '',      -- same categories as announcements
    importance INTEGER NOT NULL DEFAULT 3,
    first_seen TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS emails_received ON emails (received);

-- One row per refresh step (eLearn stream, eLearn gradebook + announcements, Outlook): when, how much, any error
CREATE TABLE IF NOT EXISTS sync_log (
    id     INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    items  INTEGER NOT NULL DEFAULT 0,
    new    INTEGER NOT NULL DEFAULT 0,
    error  TEXT NOT NULL DEFAULT ''
);

-- Courses switched off on the Announcements page: nothing from them is read or shown. No row = on.
-- Double-click a chip to switch one by hand (auto = 0); refresh.py switches off the modules you haven't starred in eLearn (auto = 1)
-- and switches them back on if you star them later. Faculty, school and career pages are never switched off automatically.
CREATE TABLE IF NOT EXISTS course_settings (
    course TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    auto   INTEGER NOT NULL DEFAULT 0 CHECK (auto IN (0, 1))
);
