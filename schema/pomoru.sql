-- Every pomoru session, written by the app (src/pomo/log.rs) into pomoru.db next to the
-- other databases. Nothing reads it yet; it is only recorded.
-- Safe to run again: it only creates what is missing, and rebuilds the view.

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY,
    mode         TEXT    NOT NULL CHECK (mode IN ('work', 'short_break', 'long_break')),
    task         TEXT,                          -- the task at the top of the list when the session began (NULL if none)
    planned_secs INTEGER NOT NULL,              -- how long the session was set for
    focused_secs INTEGER NOT NULL,              -- how long the clock actually ran
    started_at   INTEGER NOT NULL,              -- unix seconds, when it first started running
    ended_at     INTEGER NOT NULL,              -- unix seconds, when it ended
    outcome      TEXT    NOT NULL CHECK (outcome IN ('completed', 'reset', 'skipped', 'quit'))
);
-- completed = ran to zero · reset = the timer was reset or its length edited part-way ·
-- skipped = switched to another mode part-way · quit = pomoru was closed part-way

CREATE INDEX IF NOT EXISTS sessions_started ON sessions (started_at);

DROP VIEW IF EXISTS session_log;
CREATE VIEW session_log AS
SELECT id,
       datetime(started_at, 'unixepoch', 'localtime') AS started,
       datetime(ended_at,   'unixepoch', 'localtime') AS ended,
       mode,
       task,
       round(planned_secs / 60.0, 1) AS planned_min,
       round(focused_secs / 60.0, 1) AS focused_min,
       outcome
FROM sessions
ORDER BY started_at DESC;
