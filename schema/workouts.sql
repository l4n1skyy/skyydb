-- Workouts: one row per set, or per thing you did. Sets of the same exercise on
-- the same date are one exercise's sets; everything on the same date is one workout.
-- Only the exercise is needed: reps, weight and notes are there when you want
-- the detail ("Bench press 8 x 60") and can stay empty when you don't ("Bouldering").
-- `skyydb` applies this file every time it runs, so it only creates what's missing;
-- views and triggers are rebuilt so edits here take effect.

-- Filled automatically from the sets, so the add/edit forms can suggest names
CREATE TABLE IF NOT EXISTS exercises (name TEXT PRIMARY KEY COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS sets (
    id       INTEGER PRIMARY KEY,
    date     TEXT NOT NULL DEFAULT (date('now', 'localtime'))
             CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    exercise TEXT NOT NULL COLLATE NOCASE REFERENCES exercises (name) CHECK (trim(exercise) <> ''),
    reps     INTEGER CHECK (reps IS NULL OR reps > 0),    -- empty = not counted
    weight   REAL NOT NULL DEFAULT 0 CHECK (weight >= 0), -- kg, 0 = bodyweight
    notes    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sets_date ON sets (date, exercise);
CREATE INDEX IF NOT EXISTS sets_exercise ON sets (exercise, date);

-- Remember each exercise for suggestions, and spell it the way it was first
-- entered ("bench press " becomes "Bench press")
DROP TRIGGER IF EXISTS sets_exercise_insert;
CREATE TRIGGER sets_exercise_insert AFTER INSERT ON sets
BEGIN
    INSERT OR IGNORE INTO exercises (name) VALUES (trim(NEW.exercise));
    UPDATE sets SET exercise = (SELECT name FROM exercises WHERE name = trim(NEW.exercise)) WHERE id = NEW.id;
END;
DROP TRIGGER IF EXISTS sets_exercise_update;
CREATE TRIGGER sets_exercise_update AFTER UPDATE OF exercise ON sets
-- only when the spelling differs, so the trigger's own UPDATE doesn't loop
WHEN NEW.exercise COLLATE BINARY IS NOT (SELECT name FROM exercises WHERE name = trim(NEW.exercise))
BEGIN
    INSERT OR IGNORE INTO exercises (name) VALUES (trim(NEW.exercise));
    UPDATE sets SET exercise = (SELECT name FROM exercises WHERE name = trim(NEW.exercise)) WHERE id = NEW.id;
END;

-- Plans: named workouts you plan ahead ("Push day"), each a list of exercises
-- with target sets, reps (a number or a range like "8-12") and weight. All of
-- that is optional: a plan can be just a name ("Bouldering"), or a list of
-- exercise names without numbers.
CREATE TABLE IF NOT EXISTS plans (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (trim(name) <> ''),
    notes TEXT NOT NULL DEFAULT '',
    icon  TEXT NOT NULL DEFAULT ''  -- a pixel icon's name (static/fonts/pixelarticons), shown on the Stats calendar
);
CREATE TABLE IF NOT EXISTS plan_exercises (
    id       INTEGER PRIMARY KEY,
    plan_id  INTEGER NOT NULL REFERENCES plans (id),
    position INTEGER NOT NULL DEFAULT 0,
    exercise TEXT NOT NULL COLLATE NOCASE REFERENCES exercises (name) CHECK (trim(exercise) <> ''),
    sets     INTEGER CHECK (sets IS NULL OR sets > 0),
    reps     TEXT NOT NULL DEFAULT '',
    weight   REAL CHECK (weight IS NULL OR weight >= 0),  -- kg, empty = whatever you did last time
    notes    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS plan_exercises_plan ON plan_exercises (plan_id, position);

-- Exercises used to have muscle groups (removed 2026-11-21)
DROP TABLE IF EXISTS exercise_muscles;

-- The order you dragged a day's exercises into on the Log tab. Exercises not in here (logged
-- after the last drag) come after, in the order they were first logged.
CREATE TABLE IF NOT EXISTS day_order (
    date     TEXT NOT NULL,
    exercise TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (date, exercise)
);

-- Which plan is on which day: a weekday ("mon" ... "sun") repeats every week, a
-- date ("2026-11-02") is a one-off that wins over its weekday. No plan = rest day.
CREATE TABLE IF NOT EXISTS plan_schedule (
    day     TEXT PRIMARY KEY
            CHECK (day IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
                   OR day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    plan_id INTEGER REFERENCES plans (id)
);

-- Deleting a plan takes its exercises and schedule entries with it
DROP TRIGGER IF EXISTS plans_delete;
CREATE TRIGGER plans_delete AFTER DELETE ON plans
BEGIN
    DELETE FROM plan_exercises WHERE plan_id = OLD.id;
    DELETE FROM plan_schedule WHERE plan_id = OLD.id;
END;

-- Plan exercises share the exercise list (and its spelling) with the sets
DROP TRIGGER IF EXISTS plan_exercises_insert;
CREATE TRIGGER plan_exercises_insert AFTER INSERT ON plan_exercises
BEGIN
    INSERT OR IGNORE INTO exercises (name) VALUES (trim(NEW.exercise));
    UPDATE plan_exercises SET exercise = (SELECT name FROM exercises WHERE name = trim(NEW.exercise)) WHERE id = NEW.id;
END;
DROP TRIGGER IF EXISTS plan_exercises_update;
CREATE TRIGGER plan_exercises_update AFTER UPDATE OF exercise ON plan_exercises
WHEN NEW.exercise COLLATE BINARY IS NOT (SELECT name FROM exercises WHERE name = trim(NEW.exercise))
BEGIN
    INSERT OR IGNORE INTO exercises (name) VALUES (trim(NEW.exercise));
    UPDATE plan_exercises SET exercise = (SELECT name FROM exercises WHERE name = trim(NEW.exercise)) WHERE id = NEW.id;
END;

-- Every set with its set number (1st, 2nd, ... of that exercise that day)
DROP VIEW IF EXISTS set_log;
CREATE VIEW set_log AS
SELECT id, date, exercise,
       ROW_NUMBER() OVER (PARTITION BY date, exercise ORDER BY id) AS set_no,
       reps, weight, reps * weight AS volume, notes
FROM sets
ORDER BY date DESC, exercise, set_no;

-- One row per workout (date)
DROP VIEW IF EXISTS workouts;
CREATE VIEW workouts AS
SELECT date,
       CASE strftime('%w', date) WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue'
            WHEN '3' THEN 'Wed' WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri' ELSE 'Sat' END AS day,
       replace(group_concat(DISTINCT exercise), ',', ', ') AS exercises,
       count(reps) AS sets,  -- entries without reps aren't sets
       coalesce(sum(reps), 0) AS reps,
       coalesce(sum(reps * weight), 0) AS volume
FROM sets
GROUP BY date
ORDER BY date DESC;

-- Best results per exercise. est_1rm uses the Epley formula: weight * (1 + reps / 30)
DROP VIEW IF EXISTS records;
CREATE VIEW records AS
SELECT exercise,
       count(DISTINCT date) AS workouts,
       count(reps) AS sets,
       max(weight) AS heaviest,
       (SELECT max(reps) FROM sets s2 WHERE s2.exercise = s.exercise AND s2.weight = max(s.weight)) AS reps_at_heaviest,
       max(reps) AS most_reps,
       round(max(weight * (1 + reps / 30.0)), 1) AS est_1rm,
       max(date) AS last_done
FROM sets s
GROUP BY exercise
ORDER BY last_done DESC;

-- Workouts' own settings, like your Strava profile and how the last sync went
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');

-- Strava: every activity you've logged there, read by the Strava scraper (strava.py, which
-- signs in as you; Workouts > Settings starts it). A sync adds new ones and updates the
-- latest; raw keeps each activity as Strava's page gave it.
CREATE TABLE IF NOT EXISTS strava_activities (
    id           INTEGER PRIMARY KEY,           -- Strava's activity id
    name         TEXT NOT NULL DEFAULT '',
    sport        TEXT NOT NULL DEFAULT '',      -- Run, TrailRun, Ride, Walk, WeightTraining, ...
    start        TEXT NOT NULL,                 -- local start time, 'YYYY-MM-DD HH:MM:SS'
    distance     REAL NOT NULL DEFAULT 0,       -- metres
    moving_time  INTEGER NOT NULL DEFAULT 0,    -- seconds
    elapsed_time INTEGER NOT NULL DEFAULT 0,    -- seconds
    elevation    REAL NOT NULL DEFAULT 0,       -- metres climbed
    avg_hr       REAL,
    max_hr       REAL,
    raw          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS strava_start ON strava_activities (start);

-- Runs from Strava, newest first, with the pace in seconds per km
DROP VIEW IF EXISTS strava_runs;
CREATE VIEW strava_runs AS
SELECT id, date(start) AS date, name, round(distance / 1000.0, 2) AS km, moving_time,
       CASE WHEN distance > 0 THEN round(moving_time / (distance / 1000.0)) END AS pace,
       elevation, avg_hr
FROM strava_activities
WHERE sport IN ('Run', 'TrailRun', 'VirtualRun')
ORDER BY start DESC;
