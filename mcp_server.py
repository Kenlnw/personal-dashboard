"""MCP server: lets Claude (Claude Code, the Claude desktop app) read your personal data.

Read-only by design: the database is opened with SQLite's read-only mode plus
`PRAGMA query_only`, so no tool here can change anything. It reuses the API's
summary functions, so Claude sees the same numbers as the dashboard.

Run by an MCP client over stdio (it is not started by hand):
    .venv\\Scripts\\python.exe mcp_server.py
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer

import db
import main      # the API's endpoint functions (daily_summary, app_summary, ...)

MAX_ROWS = 500

server = MCPServer(
    name="personal-data",
    instructions=(
        "Read-only access to the user's personal activity database (their own data). "
        "Sources: PC app usage + AFK from ActivityWatch (raw events kept 90 days, per-app daily "
        "totals forever), iPhone steps and sleep (from Apple Health via Shortcuts), counts and "
        "releases of their GitHub docs repos (LaTeX/Typst reports, Obsidian notes), and the list "
        "of their GitHub repos, and their tasks / to-dos (tasks tool). Prefer the summary tools; use query_database for anything they "
        "don't cover (call database_schema first). Dates in summaries are the user's local time; "
        "timestamps stored in the database are UTC ISO-8601. 'PC active time' counts only time "
        "the user was not AFK."
    ),
)


def ro_conn() -> sqlite3.Connection:
    """A read-only connection (SQLite refuses writes on it)."""
    conn = sqlite3.connect(f"file:{db.DB_PATH.as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    return conn


def with_conn(fn, *args, **kwargs):
    conn = ro_conn()
    try:
        return fn(*args, conn=conn, **kwargs)
    finally:
        conn.close()


@server.tool()
def daily_overview(days: int = 7) -> list[dict]:
    """One row per day (newest first) for the last `days` days (1-3660): PC active minutes,
    the top 5 apps, steps, and hours asleep (sleep is listed under the day you woke up)."""
    return with_conn(main.daily_summary, days=max(1, min(days, 3660)), top=5)


@server.tool()
def app_usage(days: int = 7, include_afk: bool = False) -> list[dict]:
    """Minutes per PC app over the last `days` days (at most 90, raw data limit), most used
    first. By default only time the user was at the PC counts; include_afk=True gives raw
    focused-window time."""
    return with_conn(main.app_summary, days=max(1, min(days, 90)), device=None, include_afk=include_afk)


@server.tool()
def sleep_nights(days: int = 14) -> list[dict]:
    """Sleep per night (newest first): bedtime, wake time, hours asleep, whether it was
    measured as real sleep or only time in bed, awake time, and sleep stages (core/deep/rem)
    when available."""
    return with_conn(main.sleep_summary, days=max(1, min(days, 3660)), device=None)


@server.tool()
def docs_summary() -> dict:
    """The user's GitHub docs repos: files, reports (LaTeX/Typst) and notes (Obsidian) per repo,
    items per folder/subject, items added per month, and recent releases (final PDFs)."""
    return with_conn(main.docs_summary, releases=50)


@server.tool()
def github_repos() -> dict:
    """Every GitHub repo the user owns or collaborates on: description, visibility, language,
    stars, open issues, created and last-pushed dates. Most recently pushed first."""
    return with_conn(main.github_summary)


@server.tool()
def tasks(include_closed_days: int = 0, project: str | None = None) -> dict:
    """The user's tasks (their Linear-style tracker on the dashboard), shown as T-<id>:
    title, status (backlog/todo/in_progress/done/canceled), priority (1 urgent … 4 low,
    0 none), labels, project, due date, parent task. Open tasks always; tasks closed in
    the last `include_closed_days` days too. Optionally only one project (by name).
    Also returns counts: open, due today, overdue, done in the last 7 days."""
    import tasks as tasks_mod
    since = (datetime.now().astimezone() - timedelta(days=max(0, include_closed_days))).isoformat(timespec="seconds")
    conn = ro_conn()
    try:
        sql = ("SELECT t.*, p.name AS project FROM tasks t LEFT JOIN task_projects p ON p.id = t.project_id "
               "WHERE (t.status NOT IN ('done','canceled') OR t.completed_at >= ?)")
        args: list = [since]
        if project:
            sql += " AND p.name = ? COLLATE NOCASE"
            args.append(project)
        rows = conn.execute(sql + " ORDER BY t.status, CASE t.priority WHEN 0 THEN 9 ELSE t.priority END, t.due",
                            args).fetchall()
        out = []
        for r in rows:
            d = tasks_mod.task_row(r)
            d.pop("sort", None)
            d.pop("project_id", None)
            out.append(d)
        return {"summary": tasks_mod.summary(conn), "tasks": out}
    finally:
        conn.close()


@server.tool()
def focus_and_exams(days: int = 14) -> dict:
    """The user's focus timer and exams: focus minutes per day and per subject (workspace)
    for the last `days` days, today / 7-day totals, streak, recent sessions (task, minutes,
    note), and every exam (date, days left, scope, result score/max, grade, reflection, and
    "prep": focus minutes and sessions before it, review tasks done)."""
    import focus
    conn = ro_conn()
    try:
        s = focus.summary(conn, max(1, min(days, 366)))
        return {**s, "exams": focus.list_exams(conn)}
    finally:
        conn.close()


@server.tool()
def money(months: int = 3) -> dict:
    """The user's money log (yen): account balances and net worth, the monthly budget, and for
    the last `months` months the totals (spent, earned, saved into savings, net), spending and
    income per category, and every transaction (date, type, amount, account, category, note).
    Use it for spending analysis and budgeting questions (patterns, trends, where money goes,
    how much they can save). Don't give investment advice."""
    import money as m
    conn = ro_conn()
    try:
        return m.summary(conn, max(1, min(months, 24)))
    finally:
        conn.close()


@server.tool()
def recent_events(type: str = "app_usage", hours: int = 24, limit: int = 200) -> list[dict]:
    """Raw events from the last `hours` hours (newest first), e.g. type="app_usage" (app +
    window title), "afk", "web", "coding", "steps", "sleep", "nap". Times are UTC ISO-8601.
    Window titles can be personal; only fetch them when the question needs that detail."""
    since = (datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 90)))).isoformat()
    conn = ro_conn()
    try:
        rows = conn.execute(
            "SELECT device, source, type, start, end, data FROM events "
            "WHERE type = ? AND end >= ? ORDER BY start DESC LIMIT ?",
            (type, since, max(1, min(limit, MAX_ROWS)))).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]
    finally:
        conn.close()


@server.tool()
def database_schema() -> str:
    """The database's tables and columns (CREATE statements), plus notes on how data is
    stored. Call this before writing a query_database query."""
    conn = ro_conn()
    try:
        ddl = [r["sql"] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")]
        types = conn.execute("SELECT type, COUNT(*) AS n FROM events GROUP BY type ORDER BY n DESC").fetchall()
    finally:
        conn.close()
    notes = (
        "-- Notes:\n"
        "-- events.start/end are UTC ISO-8601 text (compare as strings with UTC bounds).\n"
        "-- events.data is JSON text: use json_extract(data, '$.app'), '$.title', '$.status' "
        "('afk'/'not-afk'), '$.steps', '$.hours', '$.stages_hours'.\n"
        "-- daily_app.day is the LOCAL date; active_minutes excludes AFK time.\n"
        "-- Raw app_usage/afk/web/coding events are deleted after 90 days; daily_app is kept.\n"
        "-- Event types present: " + ", ".join(f"{r['type']} ({r['n']})" for r in types)
    )
    return "\n\n".join(ddl) + "\n\n" + notes


@server.tool()
def query_database(sql: str) -> dict:
    """Run one read-only SQL query (SQLite) against the personal database and return up to
    500 rows. Writes are impossible (read-only connection). Use database_schema first."""
    conn = ro_conn()
    try:
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
        return {"columns": cols, "rows": [list(r) for r in rows[:MAX_ROWS]],
                "truncated": len(rows) > MAX_ROWS}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()


if __name__ == "__main__":
    server.run("stdio")
