-- Thoughts: little notes worth keeping, each with any number of tags. `skyydb` applies
-- this file every time it runs, so it only creates what's missing; views and triggers are
-- rebuilt so edits here take effect. quotes.sql is the same thing for quotes.
-- (This was Knowledge, with "bits", until 2026-11-20: `skyydb` renames an old knowledge.db.)

CREATE TABLE IF NOT EXISTS thoughts (
    id     INTEGER PRIMARY KEY,
    title  TEXT NOT NULL DEFAULT '',
    text   TEXT NOT NULL CHECK (trim(text) <> ''),
    source TEXT NOT NULL DEFAULT '',  -- where it came from: a link, a book, a person
    added  TEXT NOT NULL DEFAULT (date('now', 'localtime'))
           CHECK (added GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
);

CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY COLLATE NOCASE CHECK (trim(name) <> '')
);

CREATE TABLE IF NOT EXISTS thought_tags (
    thought_id INTEGER NOT NULL REFERENCES thoughts (id),
    tag        TEXT NOT NULL COLLATE NOCASE REFERENCES tags (name),
    PRIMARY KEY (thought_id, tag)
);
CREATE INDEX IF NOT EXISTS thought_tags_tag ON thought_tags (tag);

-- A tag given to a thought (in Datasette's row form, say) becomes a tag if it isn't one yet
DROP TRIGGER IF EXISTS thought_tags_new_tag;
CREATE TRIGGER thought_tags_new_tag BEFORE INSERT ON thought_tags
BEGIN
    INSERT OR IGNORE INTO tags (name) VALUES (NEW.tag);
END;

-- Renaming a tag renames it on its thoughts; deleting a tag or a thought removes their links
DROP TRIGGER IF EXISTS tags_rename;
CREATE TRIGGER tags_rename AFTER UPDATE OF name ON tags
BEGIN
    UPDATE thought_tags SET tag = NEW.name WHERE tag = OLD.name;
END;
DROP TRIGGER IF EXISTS tags_delete;
CREATE TRIGGER tags_delete AFTER DELETE ON tags
BEGIN
    DELETE FROM thought_tags WHERE tag = OLD.name;
END;
DROP TRIGGER IF EXISTS thoughts_delete;
CREATE TRIGGER thoughts_delete AFTER DELETE ON thoughts
BEGIN
    DELETE FROM thought_tags WHERE thought_id = OLD.id;
END;

-- Every thought with its tags
DROP VIEW IF EXISTS thoughts_tagged;
CREATE VIEW thoughts_tagged AS
SELECT t.id, t.title, t.text, t.source,
       (SELECT group_concat(tag, ', ') FROM (SELECT tag FROM thought_tags WHERE thought_id = t.id ORDER BY tag)) AS tags,
       t.added
FROM thoughts t
ORDER BY t.added DESC, t.id DESC;

-- How many thoughts each tag has
DROP VIEW IF EXISTS tag_counts;
CREATE VIEW tag_counts AS
SELECT t.name AS tag, count(l.thought_id) AS thoughts
FROM tags t LEFT JOIN thought_tags l ON l.tag = t.name
GROUP BY t.name
ORDER BY thoughts DESC, t.name;
