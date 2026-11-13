"""Copy new events from ActivityWatch (running on this PC) into the Personal API DB.

Run it by hand or every 15 min with Windows Task Scheduler:
    python sync_aw.py
"""
from datetime import datetime, timedelta, timezone

import httpx

import db

AW_URL = "http://localhost:5600/api/0"
DEVICE = "pc"
FIRST_SYNC_DAYS = 7                 # how far back to go on the very first run
OVERLAP = timedelta(minutes=10)     # AW keeps extending the latest event, so re-read a bit

# ActivityWatch bucket type -> our event type + which fields to keep
TYPE_MAP = {
    "currentwindow":         ("app_usage", ["app", "title"]),
    "afkstatus":             ("afk",       ["status"]),
    "web.tab.current":       ("web",       ["url", "title"]),
    "app.editor.activity":   ("coding",    ["file", "project", "language"]),
}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def convert(bucket_id: str, our_type: str, keep: list[str], ev: dict) -> dict:
    start = parse_ts(ev["timestamp"])
    end = start + timedelta(seconds=ev["duration"])
    data = {k: ev["data"][k] for k in keep if k in ev["data"]}
    if our_type == "app_usage" and "app" in data:
        data["app"] = data["app"].removesuffix(".exe")
    return {
        "device": DEVICE,
        "source": "activitywatch",
        "source_id": f"{bucket_id}:{ev['id']}",
        "type": our_type,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "data": data,
    }


def sync() -> None:
    conn = db.connect()
    now = datetime.now(timezone.utc)
    try:
        buckets = httpx.get(f"{AW_URL}/buckets/", timeout=10).json()
    except httpx.ConnectError:
        print("ActivityWatch is not running (nothing on localhost:5600).")
        return

    for bucket_id, info in buckets.items():
        if info.get("type") not in TYPE_MAP:
            continue
        our_type, keep = TYPE_MAP[info["type"]]
        key = f"aw_last_sync:{bucket_id}"
        last = db.get_state(conn, key)
        since = parse_ts(last) - OVERLAP if last else now - timedelta(days=FIRST_SYNC_DAYS)

        events = httpx.get(
            f"{AW_URL}/buckets/{bucket_id}/events",
            params={"start": since.isoformat(), "end": now.isoformat(), "limit": -1},
            timeout=60,
        ).json()
        # skip zero-length noise
        rows = [convert(bucket_id, our_type, keep, e) for e in events if e["duration"] > 0]
        n = db.upsert_events(conn, rows) if rows else 0
        db.set_state(conn, key, now.isoformat())
        print(f"{bucket_id}: {n} events")
    conn.close()


if __name__ == "__main__":
    sync()
    import maintenance          # daily rollup, 90-day cleanup, daily backup
    maintenance.run()
    import docs_sync            # GitHub docs counts + releases, at most every 3 hours
    docs_sync.run()
