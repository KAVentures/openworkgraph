"""Conditional retained-run concurrency envelopes, not live capacity estimates."""
from collections import Counter


def summarize(measured, unresolved, window_us, capacity):
    """Measured: (id, clipped start, end); unresolved: (id, earliest start).

    Each unresolved record may end anywhere between its clipped start and
    window end. The lower/upper trajectories are simultaneous extrema under
    this declared envelope, not a reconstruction of actual completion times.
    """
    if type(window_us) is not int or window_us <= 0 or type(capacity) is not int or capacity < 1:
        raise ValueError("Invalid window or capacity")
    ids = set()
    known = Counter({0: 0, window_us: 0})
    possible = Counter({0: 0, window_us: 0})
    for key, a, b in measured:
        if key in ids or type(a) is not int or type(b) is not int or not 0 <= a < b <= window_us:
            raise ValueError("Invalid measured interval")
        ids.add(key)
        known[a] += 1
        known[b] -= 1
    for key, a in unresolved:
        if key in ids or type(a) is not int or not 0 <= a < window_us:
            raise ValueError("Invalid unresolved interval")
        ids.add(key)
        possible[a] += 1
        possible[window_us] -= 1
    lower = upper_extra = previous = 0
    rows = []
    totals = Counter()
    peak_lower = peak_upper = 0
    for at in sorted(known.keys() | possible.keys()):
        if at > previous:
            upper = lower + upper_extra
            duration = at - previous
            state = ("above_in_measured" if lower > capacity else
                     "possibly_above" if upper > capacity else "not_above_in_envelope")
            totals[state] += duration
            totals["lower_run_us"] += lower * duration
            totals["upper_run_us"] += upper * duration
            peak_lower = max(peak_lower, lower)
            peak_upper = max(peak_upper, upper)
            if rows and (rows[-1]["lower_runs"], rows[-1]["upper_runs"]) == (lower, upper):
                rows[-1]["end_us"] = at
            else:
                rows.append({"start_us": previous, "end_us": at,
                             "lower_runs": lower, "upper_runs": upper,
                             "capacity_state": state})
        lower += known[at]
        upper_extra += possible[at]
        if lower < 0 or upper_extra < 0:
            raise ValueError("Invalid concurrency envelope sweep")
        previous = at
    if lower or upper_extra or sum(totals[k] for k in ("above_in_measured", "possibly_above", "not_above_in_envelope")) != window_us:
        raise ValueError("Incomplete concurrency envelope")
    if totals["lower_run_us"] != sum(b-a for _, a, b in measured) or totals["upper_run_us"] != totals["lower_run_us"] + sum(window_us-a for _, a in unresolved):
        raise ValueError("Concurrency envelope accounting failed")
    return {"summary": {"measured_intervals": len(measured), "unresolved_records": len(unresolved),
                        "window_us": window_us, "peak_lower_runs": peak_lower, "peak_upper_runs": peak_upper,
                        "measured_above_capacity_us": totals["above_in_measured"],
                        "additional_possible_above_capacity_us": totals["possibly_above"],
                        "upper_above_capacity_us": totals["above_in_measured"] + totals["possibly_above"],
                        "not_above_in_envelope_us": totals["not_above_in_envelope"],
                        "lower_run_us": str(totals["lower_run_us"]), "upper_run_us": str(totals["upper_run_us"])},
            "segments": rows}
