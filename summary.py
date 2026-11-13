"""Shared calculations, used by the API (main.py) and the daily rollup (maintenance.py).

Active time = app_usage time that overlaps ActivityWatch `not-afk` periods on the same
device. Devices with no AFK data in the window are counted as-is.
"""
import bisect
import json
from datetime import date, datetime, time, timedelta, timezone


def merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Sort intervals and merge any that overlap or touch."""
    merged: list[list[datetime]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def overlap_seconds(s: datetime, e: datetime, active: list[tuple[datetime, datetime]],
                    starts: list[datetime]) -> float:
    """Seconds of [s, e] covered by `active` (merged + sorted; `starts` = their start times)."""
    # merged intervals don't overlap, so only the last one starting at or before s
    # (and those after it) can intersect [s, e]
    i = max(bisect.bisect_right(starts, s) - 1, 0)
    total = 0.0
    for a_s, a_e in active[i:]:
        if a_s >= e:
            break
        if a_e > s:
            total += (min(e, a_e) - max(s, a_s)).total_seconds()
    return total


def utc(dt: datetime) -> datetime:
    """Aware UTC datetime (naive = PC local time). Stored times are UTC ISO strings,
    so query bounds must be UTC too for string comparison to work."""
    return (dt if dt.tzinfo else dt.astimezone()).astimezone(timezone.utc)


def local_day_bounds(d: date) -> tuple[datetime, datetime]:
    """[start, end) of a local calendar day, in UTC."""
    start = datetime.combine(d, time()).astimezone()
    end = datetime.combine(d + timedelta(days=1), time()).astimezone()
    return utc(start), utc(end)


def app_seconds(conn, since: datetime, until: datetime, device: str | None = None
                ) -> dict[tuple[str, str], list[float]]:
    """{(device, app): [active_seconds, window_seconds]} for app_usage within [since, until).
    Events crossing the window edges are clipped to it."""
    since, until = utc(since), utc(until)

    def fetch(type_: str):
        sql = "SELECT device, start, end, data FROM events WHERE type = ? AND end > ? AND start < ?"
        args = [type_, since.isoformat(), until.isoformat()]
        if device:
            sql += " AND device = ?"; args.append(device)
        for r in conn.execute(sql, args):
            s = max(datetime.fromisoformat(r["start"]), since)
            e = min(datetime.fromisoformat(r["end"]), until)
            if e > s:
                yield r["device"], s, e, json.loads(r["data"])

    raw_active: dict[str, list] = {}
    for dev, s, e, d in fetch("afk"):
        raw_active.setdefault(dev, [])
        if d.get("status") == "not-afk":
            raw_active[dev].append((s, e))
    active = {dev: merge(iv) for dev, iv in raw_active.items()}
    starts = {dev: [a for a, _ in iv] for dev, iv in active.items()}

    out: dict[tuple[str, str], list[float]] = {}
    for dev, s, e, d in fetch("app_usage"):
        window = (e - s).total_seconds()
        act = overlap_seconds(s, e, active[dev], starts[dev]) if dev in active else window
        acc = out.setdefault((dev, d.get("app") or "(unknown)"), [0.0, 0.0])
        acc[0] += act
        acc[1] += window
    return out
