"""Personal API — receives activity events from all devices and lets you query them.

Run:  uvicorn main:app --host 127.0.0.1 --port 8000
Docs: http://127.0.0.1:8000/docs
"""
import json
from contextlib import asynccontextmanager
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from pydantic import BaseModel, Field

import config
import db
import summary

TOKEN_FILE = Path(__file__).with_name(".papi_token")
ERROR_LOG = Path(__file__).with_name("validation_errors.log")

REQUEST_LOG = Path(__file__).with_name("requests.log")

@asynccontextmanager
async def lifespan(app):
    """Runs only when the server starts (not when mcp_server imports this module)."""
    import tasks
    tasks.start_generator()          # recurring tasks: create each day's task
    import threading
    import notes                     # read the notes into the search cache now, not on the first search
    threading.Thread(target=lambda: notes.search_notes(["\x00"], 1), daemon=True, name="notes-warmup").start()
    import gitsync
    gitsync.start_auto()             # PersonalDocs: pull what the iPhone / iPad pushed, every 10 min
    yield


app = FastAPI(title="Personal API", lifespan=lifespan)
# Compress JSON / JS / CSS for phones on the tailnet. Starlette already skips streamed chat
# answers (text/event-stream) and images; PDFs and other binaries are skipped too.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6,
                   exclude_content_types=DEFAULT_EXCLUDED_CONTENT_TYPES + ("application/pdf", "image/*",
                                                                          "application/octet-stream"))


@app.middleware("http")
async def log_event_requests(request: Request, call_next):
    """Log failed /events requests with the *shape* of their auth header
    (never the token itself), to debug requests from phones/Shortcuts."""
    response = await call_next(request)
    if request.url.path == "/events" and response.status_code >= 400:
        auth = request.headers.get("authorization", "")
        tok = auth[len("Bearer "):] if auth.startswith("Bearer ") else auth
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "client": request.headers.get("x-forwarded-for") or (request.client.host if request.client else None),
            "user_agent": request.headers.get("user-agent"),
            "content_type": request.headers.get("content-type"),
            "auth_present": bool(auth),
            "auth_starts_with_bearer_space": auth.startswith("Bearer "),
            "token_length": len(tok),
            "token_is_lowercase_hex": bool(tok) and all(c in "0123456789abcdef" for c in tok),
            "token_has_whitespace": any(c.isspace() for c in tok),
        }
        with REQUEST_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return response


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """Browsers must re-check /static files (cheap: ETag → 304), so dashboard updates show
    up right away instead of after the browser's heuristic cache expires."""
    response = await call_next(request)
    if request.url.path.startswith("/static/vendor/"):        # pinned third-party files (KaTeX): cache a week
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


SERVER_ERROR_LOG = Path(__file__).with_name("errors.log")


@app.exception_handler(Exception)
async def log_server_error(request: Request, exc: Exception):
    """Record the traceback of any unexpected error (HTTP 500), so it can be debugged later."""
    import traceback
    entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"),
             "method": request.method, "path": request.url.path, "query": request.url.query,
             "error": repr(exc), "traceback": traceback.format_exc()}
    with SERVER_ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return JSONResponse({"detail": "Internal Server Error (see errors.log)"}, status_code=500)


@app.exception_handler(RequestValidationError)
async def log_validation_error(request: Request, exc: RequestValidationError):
    """Record why a request was rejected (422), so problems with requests from
    phones/Shortcuts can be debugged from the PC. Headers (the token) are not logged."""
    entry = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": request.method,
        "path": request.url.path,
        "content_type": request.headers.get("content-type"),
        "errors": [{k: e.get(k) for k in ("loc", "msg", "input")} for e in exc.errors()],
    }
    raw = (await request.body()).decode("utf-8", "replace")
    entry["body_length"] = len(raw)
    entry["body_start"] = raw[:300]      # what the Shortcut actually sent (no headers/token)
    if any(e.get("type") == "json_invalid" for e in exc.errors()):
        # show the text around where the JSON broke (e.g. iPhone "smart quotes" “ ” instead of ")
        pos = next((e["loc"][1] for e in exc.errors() if e.get("type") == "json_invalid"
                    and len(e.get("loc", ())) > 1 and isinstance(e["loc"][1], int)), 0)
        entry["body_near_error"] = raw[max(pos - 40, 0): pos + 40]
    with ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return await request_validation_exception_handler(request, exc)


def current_token() -> str:
    """The API token: from the .papi_token file next to this script if it exists
    (read on every request, so a change applies without restarting), otherwise
    from the PAPI_TOKEN environment variable."""
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return os.environ.get("PAPI_TOKEN", "").strip()


# Tailscale logins allowed to use the dashboard without the token. `tailscale serve`
# adds the Tailscale-User-Login header for requests from these users' devices.
ALLOWED_LOGINS = set(config.get("allowed_logins"))     # config.json


def check_token(request: Request, authorization: str = Header(default="")):
    """Allow a request with the API token (Shortcuts, scripts), or one that came through
    `tailscale serve` from an allowed Tailscale user (the dashboard in a browser).
    uvicorn only listens on 127.0.0.1, so the identity header can only come from
    tailscale serve or from a program already running on this PC."""
    if request.headers.get("tailscale-user-login") in ALLOWED_LOGINS:
        return
    token = current_token()
    if not token:
        raise HTTPException(500, "No token set: create .papi_token (see README)")
    # constant-time compare; bytes so a non-ASCII header can't raise
    if not secrets.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Bad or missing token")


def get_conn():
    # FastAPI may open this connection in one worker thread and run the endpoint in
    # another; each request still has its own connection, used by one thread at a time.
    conn = db.connect(check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class Event(BaseModel):
    device: str = Field(examples=["iphone"])
    source: str = Field(examples=["shortcuts"])
    source_id: str = Field(examples=["sleep-2026-11-10"])
    type: str = Field(examples=["sleep"])
    start: datetime
    end: datetime
    data: dict = {}


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:          # assume local time if no timezone given
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).isoformat()


@app.post("/events", dependencies=[Depends(check_token)])
def add_events(events: list[Event] | Event, conn=Depends(get_conn)):
    """Save one event or a list of events. (A single object is accepted because
    iOS Shortcuts' JSON body builder can't send a top-level list.)"""
    if isinstance(events, Event):
        events = [events]
    rows = [
        {**e.model_dump(), "start": to_utc_iso(e.start), "end": to_utc_iso(e.end)}
        for e in events
    ]
    return {"saved": db.upsert_events(conn, rows)}


@app.get("/events", dependencies=[Depends(check_token)])
def list_events(
    start: datetime | None = None,
    end: datetime | None = None,
    type: str | None = None,
    device: str | None = None,
    limit: int = Query(500, le=10000),
    conn=Depends(get_conn),
):
    sql, args = "SELECT * FROM events WHERE 1=1", []
    if start:
        sql += " AND end >= ?"; args.append(to_utc_iso(start))
    if end:
        sql += " AND start <= ?"; args.append(to_utc_iso(end))
    if type:
        sql += " AND type = ?"; args.append(type)
    if device:
        sql += " AND device = ?"; args.append(device)
    sql += " ORDER BY start DESC LIMIT ?"; args.append(limit)
    return [{**dict(r), "data": json.loads(r["data"])} for r in conn.execute(sql, args)]


@app.get("/summary/apps", dependencies=[Depends(check_token)])
def app_summary(
    days: int = 1,
    device: str | None = None,
    include_afk: bool = False,
    conn=Depends(get_conn),
):
    """Minutes spent per app over the last `days` days (from raw app_usage events,
    so at most the last 90 days; use /summary/daily for longer ranges).

    By default only time while you were at the device counts: app time is
    intersected with ActivityWatch's `not-afk` periods. Devices with no AFK data
    at all are counted as-is. Pass `include_afk=true` for raw window time.
    """
    now = datetime.now(timezone.utc)
    secs = summary.app_seconds(conn, now - timedelta(days=days), now, device)
    minutes: dict[str, float] = {}
    for (_dev, app_name), (active, window) in secs.items():
        minutes[app_name] = minutes.get(app_name, 0.0) + (window if include_afk else active) / 60
    return [
        {"app": a, "minutes": round(m, 1)}
        for a, m in sorted(minutes.items(), key=lambda kv: -kv[1])
        if m >= 0.05
    ]


@app.get("/summary/daily", dependencies=[Depends(check_token)])
def daily_summary(days: int = Query(30, ge=1, le=3660), top: int = 5, conn=Depends(get_conn)):
    """One row per local day, newest first: PC active minutes + top apps, steps, sleep.
    Past days come from the daily_app rollup (kept forever); today is computed live."""
    today = date.today()
    first = today - timedelta(days=days - 1)
    since, _ = summary.local_day_bounds(first)

    per_day: dict[str, dict[str, float]] = {}
    for r in conn.execute("SELECT day, app, SUM(active_minutes) AS m FROM daily_app "
                          "WHERE day >= ? AND day < ? GROUP BY day, app",
                          (first.isoformat(), today.isoformat())):
        per_day.setdefault(r["day"], {})[r["app"]] = r["m"]
    t_since, t_until = summary.local_day_bounds(today)
    for (_dev, app_name), (active, _w) in summary.app_seconds(conn, t_since, t_until).items():
        per_day.setdefault(today.isoformat(), {})
        per_day[today.isoformat()][app_name] = per_day[today.isoformat()].get(app_name, 0) + active / 60

    steps: dict[str, float] = {}
    for r in conn.execute("SELECT start, data FROM events WHERE type = 'steps' AND start >= ?",
                          (since.isoformat(),)):
        day = datetime.fromisoformat(r["start"]).astimezone().date().isoformat()
        steps[day] = max(steps.get(day, 0), json.loads(r["data"]).get("steps") or 0)
    sleep: dict[str, dict] = {}
    for r in conn.execute("SELECT end, data FROM events WHERE type = 'sleep' AND end >= ?",
                          (since.isoformat(),)):
        day = datetime.fromisoformat(r["end"]).astimezone().date().isoformat()   # wake-up day
        d = json.loads(r["data"])
        sleep[day] = {"hours": d.get("hours"), "measured": d.get("measured", "asleep")}

    out = []
    for i in range(days):
        day = (today - timedelta(days=i)).isoformat()
        apps = sorted(per_day.get(day, {}).items(), key=lambda kv: -kv[1])
        out.append({
            "date": day,
            "pc_active_minutes": round(sum(m for _, m in apps), 1),
            "top_apps": [{"app": a, "minutes": round(m, 1)} for a, m in apps[:top] if m >= 0.05],
            "steps": steps.get(day),
            "sleep_hours": sleep.get(day, {}).get("hours"),
            "sleep_measured": sleep.get(day, {}).get("measured"),
        })
    return out


ASLEEP_VALUES = {"asleep", "core", "deep", "rem", "asleep unspecified", "asleep (unspecified)",
                 "asleepunspecified", "asleepcore", "asleepdeep", "asleeprem"}
INBED_VALUES = {"in bed", "inbed"}
AWAKE_VALUES = {"awake"}


class SleepSample(BaseModel):
    start: datetime
    end: datetime
    value: str = Field(examples=["Asleep"])   # Health's Sleep Analysis value


class HealthSleep(BaseModel):
    samples: list[SleepSample]
    device: str = "iphone"


def _union_hours(intervals: list[tuple[datetime, datetime]]) -> float:
    return round(sum((e - s).total_seconds() for s, e in summary.merge(intervals)) / 3600, 2)


NIGHT_GAP = timedelta(hours=3)   # a longer gap between samples starts a new sleep period


def _split_periods(samples: list[tuple[datetime, datetime, str]]) -> list[list[tuple[datetime, datetime, str]]]:
    """Group samples (sorted by start) into separate sleep periods (nights / naps):
    a new period starts when a sample begins more than NIGHT_GAP after everything so far ended."""
    periods: list[list] = []
    period_end = None
    for s, e, v in sorted(samples):
        if period_end is None or s - period_end > NIGHT_GAP:
            periods.append([])
            period_end = e
        periods[-1].append((s, e, v))
        period_end = max(period_end, e)
    return periods


@app.post("/sleep/health", dependencies=[Depends(check_token)])
def sleep_from_health(body: HealthSleep, conn=Depends(get_conn)):
    """Health 'Sleep' samples (e.g. from Sleep Cycle) sent by a Shortcut, covering one
    or several days. Samples are split into sleep periods at gaps over 3 hours. Per
    wake-up date the longest period is saved as `sleep` (sleep-<date>), any others as
    `nap`. Overlapping samples are merged, so two apps aren't double-counted. Re-sending
    days that are already saved just updates them."""
    def utc(dt: datetime) -> datetime:
        return (dt if dt.tzinfo else dt.astimezone()).astimezone(timezone.utc)

    clean = [(utc(x.start), utc(x.end), x.value) for x in body.samples if utc(x.end) > utc(x.start)]
    periods = [p for p in (_analyze_period(p) for p in _split_periods(clean)) if p]
    unknown = sorted({v for p in periods for v in p["data"].get("unknown_values", [])})
    if not periods:
        return {"ok": False, "reason": "no Asleep or In Bed samples",
                "values_received": sorted({v for _, _, v in clean})}

    by_day: dict[str, list[dict]] = {}
    for p in periods:
        by_day.setdefault(p["wake_date"], []).append(p)
    rows, nights, naps = [], [], []
    for day, ps in by_day.items():
        ps.sort(key=lambda p: -p["data"]["hours"])
        for i, p in enumerate(ps):
            kind = "sleep" if i == 0 else "nap"
            sid = f"sleep-{day}" if kind == "sleep" else f"nap-{day}-{p['start'].astimezone():%H%M}"
            rows.append({"device": body.device, "source": "health", "source_id": sid, "type": kind,
                         "start": p["start"].isoformat(), "end": p["end"].isoformat(), "data": p["data"]})
            summary_row = {"date": day, "bedtime": p["start"].astimezone().isoformat(timespec="minutes"),
                           "wake": p["end"].astimezone().isoformat(timespec="minutes"), **p["data"]}
            (nights if kind == "sleep" else naps).append(summary_row)
    db.upsert_events(conn, rows)
    nights.sort(key=lambda n: n["date"])
    out = {"ok": True, "nights": nights}
    if naps:
        out["naps"] = naps
    if unknown:
        out["unknown_values"] = unknown
    return out


class StepSample(BaseModel):
    start: datetime
    end: datetime
    value: str | float                     # Shortcuts sends "8947", "8,947" or 8947.0


class HealthSteps(BaseModel):
    samples: list[StepSample]
    device: str = "iphone"


@app.post("/steps/health", dependencies=[Depends(check_token)])
def steps_from_health(body: HealthSteps, conn=Depends(get_conn)):
    """Daily step totals from a Shortcut: Health "Steps" samples grouped by day, for one or
    several days (send the last 7 every time). Each day is saved as `steps-<date>`, the same
    event the old one-day Shortcut wrote, so re-sending only updates it and a run after missed
    days (PC asleep, phone locked) fills them in. Samples of the same day are added up."""
    import re
    per_day: dict[str, dict] = {}
    for x in body.samples:
        m = re.search(r"\d[\d,. ]*", str(x.value))
        if not m:
            continue
        n = float(m.group().replace(",", "").replace(" ", "") or 0)
        start = x.start if x.start.tzinfo else x.start.astimezone()
        day = start.astimezone().date().isoformat()
        d = per_day.setdefault(day, {"steps": 0.0, "start": start, "end": x.end})
        d["steps"] += n
        d["start"], d["end"] = min(d["start"], start), max(d["end"], x.end)
    if not per_day:
        return {"ok": False, "reason": "no step samples with a number", "received": len(body.samples)}
    rows = [{"device": body.device, "source": "shortcuts", "source_id": f"steps-{day}", "type": "steps",
             "start": to_utc_iso(d["start"]), "end": to_utc_iso(d["end"]), "data": {"steps": round(d["steps"])}}
            for day, d in per_day.items()]
    db.upsert_events(conn, rows)
    return {"ok": True, "days": {day: round(d["steps"]) for day, d in sorted(per_day.items())}}


def _analyze_period(samples: list[tuple[datetime, datetime, str]]) -> dict | None:
    """Hours asleep / in bed / awake and stages for one sleep period, or None if it has
    no Asleep or In Bed samples (e.g. only 'Awake')."""
    by_kind: dict[str, list] = {"asleep": [], "in_bed": [], "awake": []}
    stage_secs: dict[str, float] = {}
    unknown: set[str] = set()
    for s, e, value in samples:
        v = value.strip().lower()
        if v in ASLEEP_VALUES:
            by_kind["asleep"].append((s, e))
            stage = v.removeprefix("asleep").strip(" ()") or "unspecified"
            stage_secs[stage] = stage_secs.get(stage, 0) + (e - s).total_seconds()
        elif v in INBED_VALUES:
            by_kind["in_bed"].append((s, e))
        elif v in AWAKE_VALUES:
            by_kind["awake"].append((s, e))
        else:
            unknown.add(value)

    main_kind = "asleep" if by_kind["asleep"] else "in_bed"
    if not by_kind[main_kind]:
        return None

    if len(stage_secs) > 1:      # real stages present: a plain "Asleep" overlaps them
        stage_secs.pop("unspecified", None)
    everything = by_kind["asleep"] + by_kind["in_bed"]
    start, end = min(s for s, _ in everything), max(e for _, e in everything)
    data = {
        "hours": _union_hours(by_kind[main_kind]),
        "measured": main_kind,               # "in_bed" = app only logged time in bed
        "in_bed_hours": _union_hours(by_kind["in_bed"]) if by_kind["in_bed"] else None,
        "awake_hours": _union_hours(by_kind["awake"]) if by_kind["awake"] else None,
        "stages_hours": {k: round(v / 3600, 2) for k, v in stage_secs.items()} or None,
        "samples": len(samples),
    }
    if unknown:
        data["unknown_values"] = sorted(unknown)
    return {"start": start, "end": end, "wake_date": end.astimezone().date().isoformat(), "data": data}


@app.get("/summary/sleep", dependencies=[Depends(check_token)])
def sleep_summary(days: int = 7, device: str | None = None, conn=Depends(get_conn)):
    """Sleep per night over the last `days` days, newest first, in local time."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql, args = "SELECT * FROM events WHERE type = 'sleep' AND end >= ?", [since]
    if device:
        sql += " AND device = ?"; args.append(device)
    nights = []
    for r in conn.execute(sql + " ORDER BY start DESC", args):
        s = datetime.fromisoformat(r["start"]).astimezone()
        e = datetime.fromisoformat(r["end"]).astimezone()
        d = json.loads(r["data"])
        nights.append({"date": e.date().isoformat(), "bedtime": s.isoformat(timespec="minutes"),
                       "wake": e.isoformat(timespec="minutes"), "hours": d.get("hours"),
                       "measured": d.get("measured", "asleep"), "in_bed_hours": d.get("in_bed_hours"),
                       "awake_hours": d.get("awake_hours"), "stages_hours": d.get("stages_hours"),
                       "device": r["device"]})
    return nights


@app.get("/summary/docs", dependencies=[Depends(check_token)])
def docs_summary(releases: int = 20, conn=Depends(get_conn)):
    """GitHub docs repos: latest counts per repo, items added per month, and recent
    releases (final PDFs). Counts and titles only, never file contents."""
    repos = []
    for r in conn.execute("SELECT s.* FROM docs_snapshot s JOIN (SELECT repo, MAX(day) AS day "
                          "FROM docs_snapshot GROUP BY repo) l USING (repo, day) ORDER BY repo"):
        d = json.loads(r["detail"])
        groups = {g: n for g, n in d.get("per_group", {}).items()}
        repos.append({"repo": r["repo"], "as_of": r["day"], "files": r["files"], "reports": r["reports"],
                      "notes": r["notes"], "releases": r["releases"], "files_by_ext": d.get("by_ext", {}),
                      "items_per_folder": groups})
    per_month = [dict(r) for r in conn.execute(
        "SELECT substr(first_seen, 1, 7) AS month, repo, kind, COUNT(*) AS added FROM docs_items "
        "WHERE first_seen IS NOT NULL GROUP BY month, repo, kind ORDER BY month")]
    rel = [{**dict(r), "assets": json.loads(r["assets"])} for r in conn.execute(
        "SELECT repo, tag, name, published_at, url, assets FROM docs_releases "
        "ORDER BY published_at DESC LIMIT ?", (releases,))]
    return {"last_sync": db.get_state(conn, "docs_last_sync"), "repos": repos,
            "added_per_month": per_month, "recent_releases": rel}


import workspaces  # noqa: E402  (needs check_token, defined above)
app.include_router(workspaces.build_router(check_token))
import studio  # noqa: E402
app.include_router(studio.build_router(check_token))
import tasks  # noqa: E402
app.include_router(tasks.build_router(check_token))
import focus  # noqa: E402
app.include_router(focus.build_router(check_token))
import money  # noqa: E402
app.include_router(money.build_router(check_token))
import notes  # noqa: E402
app.include_router(notes.build_router(check_token))
import gitsync  # noqa: E402
app.include_router(gitsync.build_router(check_token))

STATIC_DIR = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    """The dashboard (static page; its data comes from the /summary endpoints)."""
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/whoami")
def whoami(request: Request):
    """Who `tailscale serve` says is visiting (identity headers it adds for tailnet users)."""
    return {"login": request.headers.get("tailscale-user-login"),
            "name": request.headers.get("tailscale-user-name"),
            "via_tailscale_serve": "tailscale-user-login" in request.headers}


@app.get("/summary/github", dependencies=[Depends(check_token)])
def github_summary(conn=Depends(get_conn)):
    """Every GitHub repo the account owns or collaborates on, most recently pushed first."""
    repos = []
    for r in conn.execute("SELECT * FROM github_repos ORDER BY pushed_at DESC"):
        d = dict(r)
        d["topics"] = json.loads(d["topics"])
        for k in ("private", "fork", "archived", "owned"):
            d[k] = bool(d[k])
        repos.append(d)
    langs: dict[str, int] = {}
    for r in repos:
        langs[r["language"] or "(none)"] = langs.get(r["language"] or "(none)", 0) + 1
    return {"last_sync": db.get_state(conn, "docs_last_sync"), "repos": repos,
            "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1]))}


@app.get("/health")
def health():
    return {"ok": True}
