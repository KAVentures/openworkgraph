"""Performance spectrum: how long each step-to-step segment took, over calendar time.

The performance spectrum (Denisov, Fahland and van der Aalst, 2018; in ProM and in Celonis-style
throughput analyses) draws every occurrence of a segment "A directly followed by B" as a line from
the time A happened to the time B happened. Patterns a process map hides become visible: work that
waits and is then released together (batching), cases that overtake each other (not first in,
first out), and segments that slow down in some weeks.

For the busiest segments (or the ones asked for), this returns:

* the duration quartiles of the segment, and each occurrence's class: ``fast`` (below the 25th
  percentile), ``normal``, ``slow`` (above the 75th) or ``very_slow`` (above the 95th);
* counts per class in equal calendar bins, by the time the segment started, with the median;
* ``fifo_share``: among occurrences taken in start order (pairs that start at the same instant are
  skipped), the share that also finish no earlier than the one before them; low values mean later
  work often overtakes earlier work. ``None`` when no two occurrences start at different times;
* ``batched_share``: the share of occurrences that end at the same instant as at least
  ``BATCH_MIN - 1`` others of the segment, a sign that work is released together;
* a repeatable sample of at most ``MAX_LINES`` occurrences per segment for drawing.

Durations are the time between two recorded events, not working time. The patterns describe this
log; they do not say why work waited or who caused it.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_performance_spectrum.v1"
MAX_SEGMENTS = 12
DEFAULT_SEGMENTS = 6
MAX_BINS = 100
DEFAULT_BINS = 30
MAX_LINES = 400
BATCH_MIN = 3
CLASSES = ("fast", "normal", "slow", "very_slow")


def _check(segments: Any, top: Any, bins: Any) -> list[tuple[str, str]] | None:
    if type(top) is not int or not 1 <= top <= MAX_SEGMENTS:
        raise EventLogWorkspaceError(f"top must be an integer from 1 to {MAX_SEGMENTS}")
    if type(bins) is not int or not 2 <= bins <= MAX_BINS:
        raise EventLogWorkspaceError(f"bins must be an integer from 2 to {MAX_BINS}")
    if segments is None:
        return None
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise EventLogWorkspaceError(f"segments must list 1 to {MAX_SEGMENTS} [from, to] activity pairs")
    pairs = []
    for item in segments:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not all(isinstance(a, str) and 0 < len(a) <= 200 for a in item)):
            raise EventLogWorkspaceError("each segment is a [from, to] pair of activity names")
        pairs.append((item[0], item[1]))
    return list(dict.fromkeys(pairs))


def parse_segments(values: list[str]) -> list[tuple[str, str]] | None:
    """``from->to`` strings as given in a query string."""
    if not values:
        return None
    pairs = []
    for value in values:
        if "->" not in value:
            raise EventLogWorkspaceError("a segment is written from->to, for example Check->Approve")
        a, b = value.split("->", 1)
        pairs.append([a, b])
    return pairs


def spectrum(log_id: str, case_type: str | None = None, *, segments: list[Any] | None = None,
             top: int = DEFAULT_SEGMENTS, bins: int = DEFAULT_BINS,
             cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    chosen = _check(segments, top, bins)

    def run(conn):
        conn.execute("""CREATE OR REPLACE TEMP TABLE seg AS SELECT case_id, activity AS a, ts AS t0,
              lead(activity) OVER w AS b, lead(ts) OVER w AS t1
            FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id)""")
        conn.execute("DELETE FROM seg WHERE b IS NULL")
        busiest = conn.execute("""SELECT a, b, count(*) FROM seg GROUP BY 1, 2
            ORDER BY 3 DESC, 1, 2""").fetchall()
        total_segments = len(busiest)
        counts = {(a, b): n for a, b, n in busiest}
        picked = chosen if chosen is not None else [(a, b) for a, b, _ in busiest[:top]]
        missing = [list(p) for p in picked if p not in counts]
        picked = [p for p in picked if p in counts]
        first, last = conn.execute("SELECT min(t0), max(t0) FROM seg").fetchone()
        out = []
        for a, b in picked:
            q = conn.execute("""SELECT quantile_cont(d, [0.25, 0.5, 0.75, 0.95]), avg(d), max(d) FROM (
                SELECT epoch(t1 - t0) AS d FROM seg WHERE a = ? AND b = ?)""", [a, b]).fetchone()
            q25, q50, q75, q95 = (float(v) for v in q[0])
            klass = f"""CASE WHEN d < {q25!r} THEN 'fast' WHEN d > {q95!r} THEN 'very_slow'
                         WHEN d > {q75!r} THEN 'slow' ELSE 'normal' END"""
            span = max((last - first).total_seconds(), 1.0)
            width = span / bins
            binned = conn.execute(f"""SELECT bin, count(*) FILTER (WHERE k = 'fast'), count(*) FILTER (WHERE k = 'normal'),
                  count(*) FILTER (WHERE k = 'slow'), count(*) FILTER (WHERE k = 'very_slow'), quantile_cont(d, 0.5)
                FROM (SELECT least(CAST(floor(epoch(t0 - ?::TIMESTAMP) / ?) AS INTEGER), ?) AS bin, {klass} AS k, d
                      FROM (SELECT t0, epoch(t1 - t0) AS d FROM seg WHERE a = ? AND b = ?))
                GROUP BY 1 ORDER BY 1""", [first, width, bins - 1, a, b]).fetchall()
            by_bin = {r[0]: r for r in binned}
            series = []
            for i in range(bins):
                r = by_bin.get(i)
                series.append({"bin": i, "counts": dict(zip(CLASSES, r[1:5])) if r else dict.fromkeys(CLASSES, 0),
                               "median_seconds": round(float(r[5]), 1) if r else None})
            # Only pairs whose starts differ say anything about order; simultaneous starts are skipped.
            fifo = conn.execute("""SELECT count(*) FILTER (WHERE prev_t0 < t0 AND t1 >= prev_t1),
                  count(*) FILTER (WHERE prev_t0 < t0) FROM (
                  SELECT t0, t1, lag(t0) OVER o AS prev_t0, lag(t1) OVER o AS prev_t1 FROM seg WHERE a = ? AND b = ?
                  WINDOW o AS (ORDER BY t0, t1, case_id))""", [a, b]).fetchone()
            batched = conn.execute("""SELECT coalesce(sum(n) FILTER (WHERE n >= ?), 0) FROM (
                SELECT t1, count(*) AS n FROM seg WHERE a = ? AND b = ? GROUP BY 1)""", [BATCH_MIN, a, b]).fetchone()[0]
            lines = conn.execute(f"""SELECT case_id, epoch(t0 - ?::TIMESTAMP), epoch(t1 - ?::TIMESTAMP), {klass}
                FROM (SELECT case_id, t0, t1, epoch(t1 - t0) AS d FROM seg WHERE a = ? AND b = ?)
                ORDER BY hash(case_id, t0), case_id, t0 LIMIT ?""", [first, first, a, b, MAX_LINES]).fetchall()
            n = counts[(a, b)]
            out.append({
                "from": a, "to": b, "occurrences": n,
                "duration_seconds": {"p25": round(q25, 1), "median": round(q50, 1), "p75": round(q75, 1),
                                     "p95": round(q95, 1), "mean": round(q[1], 1), "max": round(q[2], 1)},
                "bins": series,
                "fifo_share": round(fifo[0] / fifo[1], 4) if fifo[1] else None,
                "batched_share": round(int(batched) / n, 4) if n else None,
                "lines": [{"case_id": c, "start": round(s, 3), "end": round(e, 3), "class": k}
                          for c, s, e, k in sorted(lines, key=lambda r: (r[1], r[2], r[0]))],
                "lines_sampled": n > MAX_LINES,
            })
        return {"segments": out, "missing": missing, "segments_total": total_segments,
                "time_origin": first.isoformat() + "Z" if first else None,
                "bin_seconds": round(((last - first).total_seconds() if first else 0) / bins, 3) if first else None}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    if data.get("missing"):
        names = ", ".join(f"{a}->{b}" for a, b in data["missing"])
        raise EventLogWorkspaceError(f"no case goes directly from one activity to the next for: {names}")
    data.pop("missing", None)
    return {"format": FORMAT, **data, "bins": bins, "classes": {
        "fast": "shorter than the segment's 25th percentile",
        "normal": "between the 25th and 75th percentile",
        "slow": "longer than the 75th percentile",
        "very_slow": "longer than the 95th percentile"},
        "batch_min": BATCH_MIN,
        "caveat": ("Durations are the time between two recorded events, not working time. Overtaking and batching "
                   "describe this log; they do not say why work waited or who caused it.")}


__all__ = ["spectrum", "parse_segments", "FORMAT"]
