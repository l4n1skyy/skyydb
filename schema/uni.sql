-- University: courses, and the tasks in them (documents, assignments, practicals, study,
-- exams) with a deadline and one of five priorities. A finished task keeps a done_on date and moves to the Done section.
-- `skyydb` applies this file every time it runs, so it only creates what's missing; views and
-- triggers are rebuilt so edits here take effect.

CREATE TABLE IF NOT EXISTS courses (
    id     INTEGER PRIMARY KEY,
    code   TEXT NOT NULL DEFAULT '',   -- e.g. CS101
    name   TEXT NOT NULL CHECK (trim(name) <> ''),
    term   TEXT NOT NULL DEFAULT '',   -- e.g. Y3S1
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

-- The kinds of task; edited on the Sections tab
CREATE TABLE IF NOT EXISTS kinds (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE CHECK (trim(name) <> ''),
    position INTEGER NOT NULL DEFAULT 0
);
INSERT INTO kinds (name, position)
SELECT name, position FROM (SELECT 'document' AS name, 1 AS position UNION ALL SELECT 'assignment', 2
    UNION ALL SELECT 'practical', 3 UNION ALL SELECT 'study', 4 UNION ALL SELECT 'exam', 5)
WHERE NOT EXISTS (SELECT 1 FROM kinds);

-- Five priority levels, 1 = most urgent; only their names can be changed
CREATE TABLE IF NOT EXISTS priorities (
    level INTEGER PRIMARY KEY CHECK (level BETWEEN 1 AND 5),
    name  TEXT NOT NULL CHECK (trim(name) <> '')
);
INSERT OR IGNORE INTO priorities (level, name) VALUES (1, 'Urgent'), (2, 'High'), (3, 'Medium'), (4, 'Low'), (5, 'Someday');

-- The semester's calendar: week 1 starts on `start` (a Monday). break_after = the last week
-- before a one-week break, if the semester has one (NULL = none known).
CREATE TABLE IF NOT EXISTS semester (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    name        TEXT NOT NULL DEFAULT '',
    start       TEXT NOT NULL CHECK (start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    weeks       INTEGER NOT NULL DEFAULT 14,
    break_after INTEGER
);
-- Y3S1 (Sept 2026 intake): the Capstone timeline puts week 3's middle on 14 Oct, week 4 on 23 Oct,
-- week 5 on 30 Oct and week 14 on 8 Jan 2027, so week 1 began on Monday 28 Sep 2026.
INSERT OR IGNORE INTO semester (id, name, start, weeks) VALUES (1, 'Y3S1', '2026-09-28', 14);

CREATE TABLE IF NOT EXISTS tasks (
    id        INTEGER PRIMARY KEY,
    course_id INTEGER REFERENCES courses (id),
    title     TEXT NOT NULL CHECK (trim(title) <> ''),
    kind_id   INTEGER REFERENCES kinds (id),
    due       TEXT CHECK (due IS NULL OR due GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    due_time  TEXT NOT NULL DEFAULT '',            -- optional, like 23:59
    priority  INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    weight    REAL CHECK (weight IS NULL OR weight >= 0),  -- % of the course grade
    notes     TEXT NOT NULL DEFAULT '',
    added     TEXT NOT NULL DEFAULT (date('now', 'localtime')),
    done_on   TEXT                                  -- the day it was finished; NULL = still to do
);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks (due);
CREATE INDEX IF NOT EXISTS tasks_course ON tasks (course_id);

-- Deleting a course or a kind keeps its tasks, without it
DROP TRIGGER IF EXISTS courses_delete;
CREATE TRIGGER courses_delete AFTER DELETE ON courses
BEGIN
    UPDATE tasks SET course_id = NULL WHERE course_id = OLD.id;
END;
DROP TRIGGER IF EXISTS kinds_delete;
CREATE TRIGGER kinds_delete AFTER DELETE ON kinds
BEGIN
    UPDATE tasks SET kind_id = NULL WHERE kind_id = OLD.id;
END;

-- Tasks, most urgent first: by deadline (none last), then priority
DROP VIEW IF EXISTS open_tasks;
DROP VIEW IF EXISTS task_list;
CREATE VIEW task_list AS
SELECT t.id, t.course_id, coalesce(c.name, '') AS course, t.title, coalesce(k.name, '') AS kind,
       t.due, t.due_time,
       CAST(julianday(t.due) - julianday(date('now', 'localtime')) AS INTEGER) AS days_left,
       t.priority, p.name AS priority_name, t.weight, t.notes, t.done_on
FROM tasks t LEFT JOIN courses c ON c.id = t.course_id
             LEFT JOIN kinds k ON k.id = t.kind_id
             JOIN priorities p ON p.level = t.priority
WHERE t.done_on IS NULL
ORDER BY t.due IS NULL, t.due, t.priority, t.id;

-- Finished tasks, latest first
DROP VIEW IF EXISTS done_list;
CREATE VIEW done_list AS
SELECT t.id, t.course_id, coalesce(c.name, '') AS course, t.title, t.due, t.weight, t.done_on
FROM tasks t LEFT JOIN courses c ON c.id = t.course_id
WHERE t.done_on IS NOT NULL
ORDER BY t.done_on DESC, t.id DESC;

-- Per course: how many tasks and the next deadline
DROP VIEW IF EXISTS course_progress;
CREATE VIEW course_progress AS
SELECT c.id, c.code, c.name, c.term, c.active, count(t.id) AS tasks, min(t.due) AS next_due
FROM courses c LEFT JOIN tasks t ON t.course_id = c.id AND t.done_on IS NULL
GROUP BY c.id
ORDER BY c.active DESC, c.code, c.name;
