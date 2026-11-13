"""SQLite storage for the Personal API.

Every piece of activity, from any device, is stored as one row in `events`.
(source, source_id) is unique, so re-sending the same event updates it
instead of creating a duplicate.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("personal.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device    TEXT NOT NULL,          -- pc, iphone, ipad ...
    source    TEXT NOT NULL,          -- activitywatch, shortcuts, git ...
    source_id TEXT NOT NULL,          -- id inside the source (for dedupe)
    type      TEXT NOT NULL,          -- app_usage, afk, web, sleep, workout ...
    start     TEXT NOT NULL,          -- ISO 8601, UTC
    end       TEXT NOT NULL,          -- ISO 8601, UTC
    data      TEXT NOT NULL DEFAULT '{}',  -- JSON
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
CREATE INDEX IF NOT EXISTS idx_events_type  ON events(type);

-- per-app active minutes per local day, filled by maintenance.py, kept forever
-- (raw app_usage/afk events are deleted after 90 days)
CREATE TABLE IF NOT EXISTS daily_app (
    day            TEXT NOT NULL,     -- local date, YYYY-MM-DD
    device         TEXT NOT NULL,
    app            TEXT NOT NULL,
    active_minutes REAL NOT NULL,     -- while not AFK
    window_minutes REAL NOT NULL,     -- raw focused-window time
    PRIMARY KEY (day, device, app)
);

-- GitHub docs repos (docs_sync.py): counts only, never file contents
CREATE TABLE IF NOT EXISTS docs_snapshot (       -- one row per repo per day
    day      TEXT NOT NULL,
    repo     TEXT NOT NULL,
    files    INTEGER NOT NULL,
    reports  INTEGER NOT NULL,
    notes    INTEGER NOT NULL,
    releases INTEGER NOT NULL,
    detail   TEXT NOT NULL DEFAULT '{}',        -- JSON: files per extension, items per group
    PRIMARY KEY (day, repo)
);
CREATE TABLE IF NOT EXISTS docs_items (          -- each report folder / note file
    repo       TEXT NOT NULL,
    path       TEXT NOT NULL,
    kind       TEXT NOT NULL,                   -- report | note
    grp        TEXT NOT NULL,                   -- parent folder, e.g. 4yrDocs/First_Semester/Denshi
    first_seen TEXT,                            -- date of the oldest commit touching it
    last_seen  TEXT NOT NULL,                   -- last sync day it still existed
    PRIMARY KEY (repo, path)
);
CREATE TABLE IF NOT EXISTS docs_releases (
    repo         TEXT NOT NULL,
    tag          TEXT NOT NULL,
    name         TEXT,
    published_at TEXT,
    url          TEXT,
    assets       TEXT NOT NULL DEFAULT '[]',    -- JSON: [{name, size, downloads}]
    PRIMARY KEY (repo, tag)
);

-- every GitHub repo the account owns or collaborates on (docs_sync.py), metadata only
CREATE TABLE IF NOT EXISTS github_repos (
    repo        TEXT PRIMARY KEY,               -- owner/name
    description TEXT,
    private     INTEGER NOT NULL,
    fork        INTEGER NOT NULL,
    archived    INTEGER NOT NULL,
    owned       INTEGER NOT NULL,               -- 1 = the account's own repo, 0 = collaborator
    language    TEXT,
    stars       INTEGER NOT NULL DEFAULT 0,
    forks       INTEGER NOT NULL DEFAULT 0,
    open_issues INTEGER NOT NULL DEFAULT 0,
    topics      TEXT NOT NULL DEFAULT '[]',
    homepage    TEXT,
    url         TEXT NOT NULL,
    size_kb     INTEGER,
    created_at  TEXT,
    pushed_at   TEXT,
    synced_at   TEXT NOT NULL
);

-- Workspaces (workspaces.py): study spaces with sources + a Claude chat per day.
-- Files live in workspaces/<slug>/{sources,outputs,history}; these tables index them.
CREATE TABLE IF NOT EXISTS workspaces (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ws_sources (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ws       TEXT NOT NULL,
    kind     TEXT NOT NULL,                     -- file | text | url | youtube | github
    title    TEXT NOT NULL,
    filename TEXT NOT NULL,                     -- name inside sources/
    origin   TEXT,                              -- original filename, URL or repo:path
    size     INTEGER,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ws_folders (             -- linked folders: read live, read-only, never copied
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ws       TEXT NOT NULL,
    path     TEXT NOT NULL,                     -- absolute, inside one of workspaces.LINK_ROOTS
    title    TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (ws, path)
);
CREATE TABLE IF NOT EXISTS ws_days (               -- one Claude Code session per workspace per day
    ws          TEXT NOT NULL,
    day         TEXT NOT NULL,                  -- local date
    session_id  TEXT,
    summarized  INTEGER NOT NULL DEFAULT 0,     -- history/<day>.md written
    PRIMARY KEY (ws, day)
);
CREATE TABLE IF NOT EXISTS ws_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ws         TEXT NOT NULL,
    day        TEXT NOT NULL,
    role       TEXT NOT NULL,                   -- user | assistant | error
    content    TEXT NOT NULL,
    tools      TEXT NOT NULL DEFAULT '[]',      -- JSON: what Claude did (files read/written)
    cost_usd   REAL,                            -- Claude Code's own usage estimate
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ws_messages ON ws_messages(ws, day);
CREATE TABLE IF NOT EXISTS ws_jobs (                 -- Studio generations (studio.py)
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ws          TEXT NOT NULL,
    kind        TEXT NOT NULL,                  -- guide | briefing | glossary | flashcards | quiz
    options     TEXT NOT NULL DEFAULT '{}',     -- JSON: focus, count, language, sources
    status      TEXT NOT NULL,                  -- running | done | error
    title       TEXT,
    filename    TEXT,                           -- file in outputs/ when done
    error       TEXT,
    cost_usd    REAL,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

-- Tasks (tasks.py): a small Linear-style issue tracker. Shown as T-<id>.
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',      -- Markdown
    status       TEXT NOT NULL,                 -- backlog | todo | in_progress | done | canceled
    priority     INTEGER NOT NULL DEFAULT 0,    -- 0 none, 1 urgent, 2 high, 3 medium, 4 low
    labels       TEXT NOT NULL DEFAULT '[]',    -- JSON list of task_labels.name
    project_id   INTEGER,
    parent_id    INTEGER,                       -- sub-issue of
    workspace    TEXT,                          -- linked workspace (workspaces.slug)
    origin       TEXT,                          -- suggestion it came from, e.g. chat:<msg>:0.1, plan:<file>:2
    due          TEXT,                          -- local date YYYY-MM-DD
    sort         REAL NOT NULL DEFAULT 0,       -- manual order inside a status
    created_at   TEXT NOT NULL,                 -- local time with offset
    updated_at   TEXT NOT NULL,
    completed_at TEXT                           -- when it became done / canceled
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS task_projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_labels (
    name  TEXT PRIMARY KEY COLLATE NOCASE,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_recurrences (        -- repeating tasks: one task per scheduled day
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    priority     INTEGER NOT NULL DEFAULT 0,
    labels       TEXT NOT NULL DEFAULT '[]',
    project_id   INTEGER,
    workspace    TEXT,
    link         TEXT,                          -- dashboard link put in each task, e.g. #/workspaces/x?open=cards.json
    link_label   TEXT,
    weekdays     TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]',   -- JSON, 0 = Monday … 6 = Sunday
    start        TEXT NOT NULL,                 -- first day (local date)
    end          TEXT,                          -- last day, or NULL = no end
    close_missed INTEGER NOT NULL DEFAULT 1,    -- cancel a day's task when the day is over and it isn't done
    paused       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_activity (           -- history + comments of each task
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,                   -- created | comment | status | priority | title | ...
    data       TEXT NOT NULL DEFAULT '{}',      -- JSON {from, to}
    body       TEXT,                            -- comment text
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_activity ON task_activity(task_id);

-- Focus sessions and exams (focus.py)
CREATE TABLE IF NOT EXISTS focus_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER,                    -- the task worked on (optional)
    workspace       TEXT,                       -- subject (from the task, or chosen)
    title           TEXT NOT NULL,
    planned_minutes INTEGER,                    -- 25 / 50 / 90, or NULL = open-ended
    started_at      TEXT NOT NULL,              -- local time with offset
    ended_at        TEXT,                       -- NULL while running (one at a time)
    minutes         REAL,                       -- length when stopped (can be corrected)
    note            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_focus_started ON focus_sessions(started_at);
CREATE TABLE IF NOT EXISTS exams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace   TEXT,                           -- subject
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'exam',   -- exam | quiz | report | presentation | other
    date        TEXT NOT NULL,                  -- local date
    scope       TEXT NOT NULL DEFAULT '',
    score       REAL,                           -- result, once known
    max_score   REAL DEFAULT 100,
    grade       TEXT NOT NULL DEFAULT '',
    reflection  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- money (yen, whole numbers); see money.py
CREATE TABLE IF NOT EXISTS money_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    icon        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'bank',   -- wallet | bank | cash | savings | other
    archived    INTEGER NOT NULL DEFAULT 0,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    icon        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'expense', -- expense | income
    archived    INTEGER NOT NULL DEFAULT 0,
    sort        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS money_tx (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,                -- local date
    type           TEXT NOT NULL,                -- expense | income | transfer | adjust
    amount         INTEGER NOT NULL,             -- yen; signed only for adjust
    account_id     INTEGER NOT NULL,
    to_account_id  INTEGER,                      -- transfer target
    category_id    INTEGER,
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_money_tx_date ON money_tx(date);

-- small key/value table, e.g. last sync time per ActivityWatch bucket
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path = DB_PATH, check_same_thread: bool = True) -> sqlite3.Connection:
    # timeout: wait up to 10 s for a lock instead of failing ("database is locked")
    conn = sqlite3.connect(path, timeout=10, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    # WAL: the server's reads and the sync job's writes don't block each other
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


# columns added after a table was first created (CREATE TABLE IF NOT EXISTS won't add them)
COLUMNS = {
    "tasks": {"workspace": "TEXT", "origin": "TEXT"},
    "workspaces": {"model": "TEXT"},                 # Claude model for chat + Studio (NULL = default)
}


def migrate(conn: sqlite3.Connection) -> None:
    for table, cols in COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:     # another connection added it just now
                    pass
    # one task per suggestion / recurring day (NULLs don't count as duplicates)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_origin ON tasks(origin)")
    conn.commit()


def upsert_events(conn: sqlite3.Connection, events: list[dict]) -> int:
    rows = [
        (e["device"], e["source"], str(e["source_id"]), e["type"],
         e["start"], e["end"], json.dumps(e.get("data", {}), ensure_ascii=False))
        for e in events
    ]
    conn.executemany(
        """INSERT INTO events (device, source, source_id, type, start, end, data)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (source, source_id) DO UPDATE SET
               end = excluded.end, data = excluded.data, type = excluded.type""",
        rows,
    )
    conn.commit()
    return len(rows)


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key, value):
    conn.execute(
        "INSERT INTO state (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
