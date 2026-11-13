"""Focus sessions and exams.

Focus session: a timed work block, usually started from a task. The timer lives here (a row
with ended_at NULL), so it can be started on one device and stopped on another. One session
runs at a time. Its subject is a workspace (taken from the task when there is one).

Exam: a dated assessment in a workspace (exam / quiz / report / presentation), with an
optional scope, and later a result (score / max, grade, reflection). Each exam reports its
"prep": focus time in that workspace before it (since the previous exam in the same workspace,
at most 90 days) and how many of the workspace's recurring review tasks were done.

Times are stored as local ISO-8601 with offset, so substr(x, 1, 10) is the local date.
"""
import json
from datetime import date, datetime, timedelta
from datetime import date as Day     # for fields named "date" (a field would shadow the type)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from tasks import check_workspace, get_conn, get_task, log, now_iso

KINDS = ["exam", "quiz", "report", "presentation", "other"]
PREP_DAYS = 90


def _now() -> datetime:
    return datetime.now().astimezone()


def active(conn):
    return conn.execute("SELECT * FROM focus_sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()


def session_row(conn, r) -> dict:
    d = dict(r)
    if d["ended_at"] is None:
        d["elapsed_seconds"] = max(0, int((_now() - datetime.fromisoformat(d["started_at"])).total_seconds()))
    t = conn.execute("SELECT id, title, status FROM tasks WHERE id = ?", (d["task_id"],)).fetchone() if d["task_id"] else None
    d["task"] = {"id": t["id"], "key": f"T-{t['id']}", "title": t["title"], "status": t["status"]} if t else None
    w = conn.execute("SELECT name FROM workspaces WHERE slug = ?", (d["workspace"],)).fetchone() if d["workspace"] else None
    d["workspace_name"] = w["name"] if w else None
    return d


def ws_names(conn) -> dict:
    return {r["slug"]: r["name"] for r in conn.execute("SELECT slug, name FROM workspaces")}


def summary(conn, days: int = 14) -> dict:
    """Minutes per day (and per workspace) for the last `days` days, today / week totals,
    streak, and recent sessions. A running session counts up to now."""
    today = date.today()
    first = today - timedelta(days=days - 1)
    names = ws_names(conn)
    per_day = {(first + timedelta(days=i)).isoformat(): {} for i in range(days)}
    rows = conn.execute("SELECT * FROM focus_sessions WHERE substr(started_at, 1, 10) >= ?", (first.isoformat(),)).fetchall()
    for r in rows:
        mins = r["minutes"] if r["ended_at"] else (_now() - datetime.fromisoformat(r["started_at"])).total_seconds() / 60
        day = r["started_at"][:10]
        if day in per_day:
            key = r["workspace"] or ""
            per_day[day][key] = per_day[day].get(key, 0) + mins
    week_from = (today - timedelta(days=6)).isoformat()
    by_ws: dict[str, float] = {}
    for day, ws in per_day.items():
        if day >= week_from:
            for k, m in ws.items():
                by_ws[k] = by_ws.get(k, 0) + m
    streak, d = 0, today
    all_days = {r[0] for r in conn.execute("SELECT DISTINCT substr(started_at, 1, 10) FROM focus_sessions")}
    if today.isoformat() not in all_days:
        d = today - timedelta(days=1)                # today isn't over: the streak can still continue
    while d.isoformat() in all_days:
        streak, d = streak + 1, d - timedelta(days=1)
    recent = [session_row(conn, r) for r in conn.execute(
        "SELECT * FROM focus_sessions WHERE ended_at IS NOT NULL ORDER BY started_at DESC LIMIT 25")]
    return {
        "today": today.isoformat(),
        "today_minutes": round(sum(per_day[today.isoformat()].values()), 1),
        "week_minutes": round(sum(by_ws.values()), 1),
        "streak_days": streak,
        "per_day": [{"date": day, "minutes": round(sum(ws.values()), 1),
                     "by_workspace": {k: round(v, 1) for k, v in ws.items()}} for day, ws in per_day.items()],
        "week_by_workspace": sorted(({"workspace": k or None, "name": names.get(k, "No subject" if not k else k),
                                     "minutes": round(v, 1)} for k, v in by_ws.items()), key=lambda x: -x["minutes"]),
        "recent": recent,
        "active": session_row(conn, a) if (a := active(conn)) else None,
    }


def exam_row(conn, e, today: date | None = None) -> dict:
    today = today or date.today()
    d = dict(e)
    exam_day = date.fromisoformat(e["date"])
    d["days_left"] = (exam_day - today).days
    prev = conn.execute("SELECT MAX(date) FROM exams WHERE workspace IS ? AND date < ? AND id != ?",
                        (e["workspace"], e["date"], e["id"])).fetchone()[0]
    start = exam_day - timedelta(days=PREP_DAYS)
    if prev:
        start = max(start, date.fromisoformat(prev) + timedelta(days=1))
    mins, n = conn.execute(
        "SELECT COALESCE(SUM(minutes), 0), COUNT(*) FROM focus_sessions WHERE workspace IS ? AND ended_at IS NOT NULL "
        "AND substr(started_at, 1, 10) BETWEEN ? AND ?", (e["workspace"], start.isoformat(), e["date"])).fetchone()
    last = min(exam_day, today).isoformat()
    rec_total, rec_done = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status = 'done'), 0) FROM tasks WHERE origin LIKE 'rec:%' AND workspace IS ? "
        "AND due BETWEEN ? AND ?", (e["workspace"], start.isoformat(), last)).fetchone() if e["workspace"] else (0, 0)
    d["prep"] = {"from": start.isoformat(), "minutes": round(mins, 1), "sessions": n,
                 "review_done": rec_done, "review_scheduled": rec_total}
    d["percent"] = round(100 * e["score"] / e["max_score"], 1) if e["score"] is not None and e["max_score"] else None
    w = conn.execute("SELECT name FROM workspaces WHERE slug = ?", (e["workspace"],)).fetchone() if e["workspace"] else None
    d["workspace_name"] = w["name"] if w else None
    return d


def list_exams(conn, workspace: str | None = None) -> list[dict]:
    """Upcoming exams (soonest first), then past ones (latest first)."""
    sql, args = "SELECT * FROM exams", []
    if workspace:
        sql, args = sql + " WHERE workspace = ?", [workspace]
    today = date.today().isoformat()
    rows = conn.execute(sql, args).fetchall()
    up = sorted((r for r in rows if r["date"] >= today), key=lambda r: r["date"])
    past = sorted((r for r in rows if r["date"] < today), key=lambda r: r["date"], reverse=True)
    return [exam_row(conn, r) for r in up + past]


def hm(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h} h {m} min" if h else f"{m} min"


def workspace_context(conn, slug: str) -> str:
    """For Claude in a workspace: its exams and how much the user has studied for it."""
    today = date.today()
    exams = list_exams(conn, slug)
    week_from = (today - timedelta(days=6)).isoformat()
    week, today_m = conn.execute(
        "SELECT COALESCE(SUM(minutes), 0), COALESCE(SUM(CASE WHEN substr(started_at, 1, 10) = ? THEN minutes END), 0) "
        "FROM focus_sessions WHERE workspace = ? AND ended_at IS NOT NULL AND substr(started_at, 1, 10) >= ?",
        (today.isoformat(), slug, week_from)).fetchone()
    if not exams and not week:
        return ""
    lines = ["# Exams and study time (the user's focus timer on the dashboard)",
             f"Focus time in this workspace: today {hm(today_m)}, last 7 days {hm(week)}."]
    up = [e for e in exams if e["days_left"] >= 0]
    past = [e for e in exams if e["days_left"] < 0][:5]
    for e in up:
        p = e["prep"]
        rev = f"; review tasks done {p['review_done']}/{p['review_scheduled']} so far" if p["review_scheduled"] else ""
        lines.append(f"- Upcoming {e['kind']}: {e['title']} on {e['date']} (in {e['days_left']} days)"
                     f"{f', scope: ' + e['scope'] if e['scope'] else ''}. Studied for it so far: {hm(p['minutes'])} in "
                     f"{p['sessions']} sessions since {p['from']}{rev}.")
    for e in past:
        res = f"{e['score']:g}/{e['max_score'] or 100:g}" if e["score"] is not None else "no result entered"
        grade = f" ({e['grade']})" if e["grade"] else ""
        reflection = f". Reflection: {e['reflection']}" if e["reflection"] else ""
        lines.append(f"- Past {e['kind']}: {e['title']} on {e['date']}: {res}{grade}, "
                     f"after {hm(e['prep']['minutes'])} of study{reflection}")
    lines.append("When asked whether they're on track, compare the time left and study so far with the scope.")
    return "\n".join(lines)


# ---------------------------------------------------------------- API

class StartIn(BaseModel):
    task_id: int | None = None
    workspace: str | None = None
    title: str | None = Field(None, max_length=300)
    planned_minutes: int | None = Field(None, ge=1, le=600)


class StopIn(BaseModel):
    minutes: float | None = Field(None, ge=0, le=1440)     # correct the length (e.g. forgot to stop)
    note: str = Field("", max_length=2000)
    mark_done: bool = False
    discard: bool = False


class SessionPatch(BaseModel):
    minutes: float | None = Field(None, ge=0, le=1440)
    note: str | None = Field(None, max_length=2000)
    workspace: str | None = None


class ExamIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = "exam"
    date: Day
    workspace: str | None = None
    scope: str = Field("", max_length=1000)
    score: float | None = None
    max_score: float | None = 100
    grade: str = Field("", max_length=20)
    reflection: str = Field("", max_length=5000)


class ExamPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    kind: str | None = None
    date: Day | None = None
    workspace: str | None = None
    scope: str | None = Field(None, max_length=1000)
    score: float | None = None
    max_score: float | None = None
    grade: str | None = Field(None, max_length=20)
    reflection: str | None = Field(None, max_length=5000)


def build_router(check_token) -> APIRouter:
    r = APIRouter(tags=["focus"], dependencies=[Depends(check_token)])

    @r.get("/focus/current")
    def current(conn=Depends(get_conn)):
        a = active(conn)
        return {"active": session_row(conn, a) if a else None}

    @r.post("/focus/start")
    def start(body: StartIn, conn=Depends(get_conn)):
        if a := active(conn):
            raise HTTPException(409, f"A focus session is already running: {a['title']}")
        title, ws = body.title, body.workspace
        if body.task_id is not None:
            t = get_task(conn, body.task_id)
            title, ws = title or t["title"], ws or t["workspace"]
        check_workspace(conn, ws)
        if not title:
            title = (ws_names(conn).get(ws) if ws else None) or "Focus"
        cur = conn.execute("INSERT INTO focus_sessions (task_id, workspace, title, planned_minutes, started_at) "
                           "VALUES (?, ?, ?, ?, ?)", (body.task_id, ws, title.strip(), body.planned_minutes, now_iso()))
        conn.commit()
        return session_row(conn, conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (cur.lastrowid,)).fetchone())

    @r.post("/focus/stop")
    def stop(body: StopIn, conn=Depends(get_conn)):
        a = active(conn)
        if not a:
            raise HTTPException(409, "No focus session is running")
        if body.discard:
            conn.execute("DELETE FROM focus_sessions WHERE id = ?", (a["id"],))
            conn.commit()
            return {"discarded": a["id"]}
        ended = _now()
        elapsed = (ended - datetime.fromisoformat(a["started_at"])).total_seconds() / 60
        minutes = round(body.minutes if body.minutes is not None else elapsed, 1)
        conn.execute("UPDATE focus_sessions SET ended_at = ?, minutes = ?, note = ? WHERE id = ?",
                     (ended.isoformat(timespec="seconds"), minutes, body.note.strip(), a["id"]))
        if a["task_id"] and conn.execute("SELECT 1 FROM tasks WHERE id = ?", (a["task_id"],)).fetchone():
            log(conn, a["task_id"], "focus", {"minutes": minutes, "session": a["id"]}, body=body.note.strip() or None)
            t = get_task(conn, a["task_id"])
            if body.mark_done and t["status"] not in ("done", "canceled"):
                ts = now_iso()
                conn.execute("UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?", (ts, ts, t["id"]))
                log(conn, t["id"], "status", {"from": t["status"], "to": "done"})
        conn.commit()
        return session_row(conn, conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (a["id"],)).fetchone())

    @r.get("/focus/summary")
    def focus_summary(days: int = 14, conn=Depends(get_conn)):
        return summary(conn, max(1, min(days, 366)))

    @r.patch("/focus/sessions/{session_id}")
    def edit_session(session_id: int, body: SessionPatch, conn=Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM focus_sessions WHERE id = ? AND ended_at IS NOT NULL", (session_id,)).fetchone():
            raise HTTPException(404, "No such finished session")
        sets = body.model_dump(exclude_unset=True)
        if "workspace" in sets:
            check_workspace(conn, sets["workspace"])
        if sets:
            conn.execute(f"UPDATE focus_sessions SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), session_id))
            conn.commit()
        return session_row(conn, conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (session_id,)).fetchone())

    @r.delete("/focus/sessions/{session_id}")
    def delete_session(session_id: int, conn=Depends(get_conn)):
        conn.execute("DELETE FROM focus_sessions WHERE id = ?", (session_id,))
        conn.commit()
        return {"deleted": session_id}

    # ---- exams
    @r.get("/exams")
    def exams(workspace: str | None = None, conn=Depends(get_conn)):
        return list_exams(conn, workspace)

    def check_exam(conn, kind, max_score):
        if kind is not None and kind not in KINDS:
            raise HTTPException(400, f"kind must be one of {KINDS}")
        if max_score is not None and max_score <= 0:
            raise HTTPException(400, "max_score must be more than 0")

    @r.post("/exams")
    def create_exam(body: ExamIn, conn=Depends(get_conn)):
        check_exam(conn, body.kind, body.max_score)
        check_workspace(conn, body.workspace)
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO exams (workspace, title, kind, date, scope, score, max_score, grade, reflection, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (body.workspace, body.title.strip(), body.kind, body.date.isoformat(), body.scope.strip(), body.score,
             body.max_score, body.grade.strip(), body.reflection.strip(), ts, ts))
        conn.commit()
        return exam_row(conn, conn.execute("SELECT * FROM exams WHERE id = ?", (cur.lastrowid,)).fetchone())

    @r.patch("/exams/{exam_id}")
    def update_exam(exam_id: int, body: ExamPatch, conn=Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone():
            raise HTTPException(404, "No such exam")
        sets = body.model_dump(exclude_unset=True)
        check_exam(conn, sets.get("kind"), sets.get("max_score"))
        if "workspace" in sets:
            check_workspace(conn, sets["workspace"])
        if sets.get("title") is None:
            sets.pop("title", None)
        sets = {k: (v.isoformat() if isinstance(v, date) else v.strip() if isinstance(v, str) else v) for k, v in sets.items()}
        if sets:
            sets["updated_at"] = now_iso()
            conn.execute(f"UPDATE exams SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), exam_id))
            conn.commit()
        return exam_row(conn, conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone())

    @r.delete("/exams/{exam_id}")
    def delete_exam(exam_id: int, conn=Depends(get_conn)):
        conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
        conn.commit()
        return {"deleted": exam_id}

    return r
