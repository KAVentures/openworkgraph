"""A self-contained HTML report for one saved event log.

The report bundles the overview, KPIs, the most frequent variants and activity
frequencies into one file an analyst can save or share. It is a snapshot of
descriptive statistics over the events as imported (with the applied activity
mapping and an optional case filter); nothing in it is a verified business
outcome. The file has no scripts and no external resources, so it renders the
same offline and can be attached to an email or ticket.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any

from . import event_log_analytics as analytics
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-report.v1"
MAX_VARIANTS = 50
_CAVEAT = ("Descriptive statistics over the imported events. Durations are observed time between the first and last "
           "event of a case, not verified effort or waiting. Repeated activities are not verified rework. Check the "
           "underlying cases before acting on any figure.")


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.1f}%"


def _num(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else escape(str(value))


def _table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    head = "".join(f'<th{" class=n" if i in numeric else ""}>{escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td{" class=n" if i in numeric else ""}>{cell}</td>' for i, cell in enumerate(row))
                   + "</tr>" for row in rows)
    if not rows:
        body = f'<tr><td colspan="{len(headers)}">No data.</td></tr>'
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _bar(share: float) -> str:
    width = max(0.0, min(1.0, share)) * 100
    return f'<span class="bar"><span style="width:{width:.1f}%"></span></span>'


MAX_SLOW_TRANSITIONS = 10


def _costs(log_id: str, case_type: str | None, cohort: dict[str, Any] | None) -> dict[str, Any] | None:
    """The log's saved cost model applied to the report's cases, or None when no model is saved."""
    from . import event_log_costs as costs
    saved = costs.saved_model(log_id)
    if saved is None:
        return None
    spec = {**saved["model"], "cohort": cohort or {}, "limit": 5}
    if case_type:
        spec["case_type"] = case_type
    return {**costs.costs(log_id, spec), "model_saved_at": saved["saved_at"]}


def slowest_transitions(log_id: str, case_type: str | None = None,
                        cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    """Directly-follows transitions ranked by median gap, among those seen in enough cases to be stable.

    A transition qualifies when at least 3 cases (and 1% of cases) contain it. The gap is the time
    between two consecutive recorded events; it is elapsed time, not measured work or waiting.
    """
    def run(conn):
        cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        floor = max(3, -(-cases // 100))
        rows = conn.execute(f"""SELECT activity, nxt, count(*) AS n, count(DISTINCT case_id) AS c,
              quantile_cont(gap, 0.5) AS median, quantile_cont(gap, 0.9) AS p90 FROM (
                SELECT case_id, activity, lead(activity) OVER w AS nxt, epoch((lead(ts) OVER w) - ts) AS gap
                FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
            WHERE nxt IS NOT NULL GROUP BY 1, 2 HAVING count(DISTINCT case_id) >= ?
            ORDER BY median DESC, c DESC, 1, 2 LIMIT {MAX_SLOW_TRANSITIONS}""", [floor]).fetchall()
        return {"minimum_cases": floor, "transitions": [
            {"from": a, "to": b, "transitions": n, "cases": c, "median_gap_seconds": med, "p90_gap_seconds": p90}
            for a, b, n, c, med, p90 in rows]}
    return workspace._query_filtered(log_id, case_type, cohort, run)


def build(log_id: str, case_type: str | None = None, cohort: dict[str, Any] | None = None, *,
          sla_seconds: int | None = None, variant_limit: int = 15) -> dict[str, Any]:
    """Collect the report's data; ``render`` turns it into HTML."""
    if not isinstance(variant_limit, int) or isinstance(variant_limit, bool) or not 1 <= variant_limit <= MAX_VARIANTS:
        raise workspace.EventLogWorkspaceError(f"variant_limit must be an integer between 1 and {MAX_VARIANTS}")
    info = next((log for log in workspace.list_logs()["logs"] if log["log_id"] == log_id), None)
    if info is None:
        raise workspace.EventLogWorkspaceError("unknown event log")
    summary = workspace.summary(log_id, case_type, cohort)
    return {
        "format": FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "log": {"log_id": info["log_id"], "name": info["name"], "source_format": info["source_format"],
                "imported_at": info["imported_at"]},
        "case_type": case_type,
        "cohort": summary.get("cohort"),
        "cases_before_filter": summary.get("cases_before_filter"),
        "summary": summary,
        "kpis": analytics.kpis(log_id, case_type, sla_seconds, None, cohort),
        "variants": workspace.variants(log_id, case_type, variant_limit, cohort=cohort),
        "activities": workspace.activities(log_id, case_type, cohort),
        "slowest_transitions": slowest_transitions(log_id, case_type, cohort),
        "costs": _costs(log_id, case_type, cohort),
        "insights": _insights(log_id, case_type, cohort, sla_seconds),
        "interpretation": _CAVEAT,
    }


def _insights(log_id: str, case_type: str | None, cohort: dict[str, Any] | None,
              sla_seconds: int | None) -> dict[str, Any]:
    from . import event_log_insights as insights
    spec: dict[str, Any] = {"limit": 5, "cohort": cohort or {}}
    if case_type:
        spec["case_type"] = case_type
    if sla_seconds:
        spec["sla_seconds"] = sla_seconds
    return insights.insights(log_id, spec)


def _insight_section(found: dict[str, Any] | None) -> str:
    if not found or not found.get("findings"):
        return ""
    items = "".join(
        f'<li><strong>{escape(f["title"])}</strong><br><span class="meta">{_num(f["cases"])} cases '
        f'({_pct(f["case_share"])}){" · about " + _duration(f["impact_seconds"]) + " of case time involved" if f["impact_seconds"] else ""}. '
        f'{escape(f["caveat"])}</span></li>' for f in found["findings"])
    return f"""<h2>Where to look first</h2>
<ol class="insights">{items}</ol>
<p class="meta">{escape(found["interpretation"])}</p>
"""


def _cost_section(c: dict[str, Any] | None) -> str:
    if not c:
        return ""
    money = lambda v: "–" if v is None else f"{escape(c['currency'])} {v:,.2f}"
    per = c["cost_per_case"]
    rows = [[escape(a["activity"]), _num(a["events"]),
             "no price" if a["unit_cost"] is None else money(a["unit_cost"]) + (" (default)" if a["price_source"] == "default" else ""),
             money(a["cost"]), _pct(a["share_of_activity_cost"])]
            for a in c["activities"][:10]]
    unpriced = ("" if not c["unpriced_events"] else
                f'<p class="meta">No price for {escape(", ".join(a["activity"] for a in c["unpriced_activities"][:10]))}: '
                f'{_num(c["unpriced_events"])} executions are left out of the totals.</p>')
    return f"""<h2>Process cost</h2>
<p class="meta">Your saved cost model (saved {escape(c["model_saved_at"])}) applied to these cases. The log does not record what work cost.</p>
<div class="tiles"><div class="tile"><div class="v">{money(c["total_cost"])}</div><div class="k">Total</div></div>
<div class="tile"><div class="v">{money(per["mean"])}</div><div class="k">Per case (mean)</div></div>
<div class="tile"><div class="v">{money(per["median"])}</div><div class="k">Per case (median)</div></div>
<div class="tile"><div class="v">{_pct(c["repeated_execution_share"])}</div><div class="k">Spent on repeated executions</div></div></div>
{unpriced}
<div class="scroll">{_table(["Activity", "Executions", "Unit cost", "Cost", "Share"], rows, {1, 2, 3, 4})}</div>
"""


def render(data: dict[str, Any]) -> str:
    log, summary, kpis = data["log"], data["summary"], data["kpis"]
    variants, activities = data["variants"], data["activities"]["activities"]
    durations = summary["case_duration_seconds"]
    tiles = [("Cases", _num(summary["cases"])), ("Events", _num(summary["events"])),
             ("Activities", _num(summary["activities"])), ("Variants", _num(variants["distinct_variants"])),
             ("Median case duration", _duration(durations["median"])), ("P90 case duration", _duration(durations["p90"])),
             ("Repeated-activity cases", _pct(kpis.get("rework_rate"))),
             ("Events per case", _num(kpis.get("events_per_case")))]
    if kpis.get("sla"):
        tiles.append((f"Over {_duration(kpis['sla']['seconds'])}", _pct(kpis["sla"]["breach_rate"])))
    tile_html = "".join(f'<div class="tile"><div class="v">{v}</div><div class="k">{escape(k)}</div></div>' for k, v in tiles)

    filters = []
    if data.get("case_type"):
        filters.append(f"case notion <b>{escape(str(data['case_type']))}</b>")
    if data.get("cohort"):
        before = data.get("cases_before_filter")
        filters.append(f"filtered to {_num(summary['cases'])} of {_num(before)} cases: "
                       f"<code>{escape(str(data['cohort']))}</code>")
    filter_html = f'<p class="meta">{"; ".join(filters)}</p>' if filters else ""

    variant_rows = [[str(i), escape(" → ".join(v["path"])), _num(v["cases"]),
                     f'{_bar(v["share"])} {_pct(v["share"])}', _duration(v["median_duration_seconds"])]
                    for i, v in enumerate(variants["variants"], 1)]
    top = max((a["events"] for a in activities), default=0) or 1
    activity_rows = [[escape(a["activity"]), f'{_bar(a["events"] / top)} {_num(a["events"])}', _num(a["cases"]),
                      _num(a["repeated_in_cases"]), _num(a["resources"])] for a in activities]
    slow = data.get("slowest_transitions") or {"transitions": [], "minimum_cases": 3}
    slow_rows = [[escape(t["from"]) + " → " + escape(t["to"]), _duration(t["median_gap_seconds"]),
                  _duration(t["p90_gap_seconds"]), _num(t["cases"])] for t in slow["transitions"]]
    weekly_rows = [[escape(w["week_starting"]), _num(w["cases"]), _duration(w["median_throughput_seconds"])]
                   for w in kpis.get("completed_per_week") or []]
    shown = sum(v["cases"] for v in variants["variants"])
    coverage = _pct(shown / variants["cases"]) if variants["cases"] else "–"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="OpenWorkGraph {escape(FORMAT)}">
<title>{escape(log["name"])} – process report</title>
<style>
:root{{--fg:#1d2330;--muted:#5d6678;--line:#dde1e8;--bg:#fff;--soft:#f4f6f9;--accent:#2f6fde}}
@media (prefers-color-scheme: dark){{:root{{--fg:#e7eaf0;--muted:#a3abba;--line:#343b48;--bg:#14171d;--soft:#1d2129;--accent:#7aa7ff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:17px;margin:32px 0 8px}}
.meta{{color:var(--muted);margin:2px 0}} code{{font-size:12px;word-break:break-all}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:16px}}
.tile{{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:10px 12px}}
.tile .v{{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}} .tile .k{{color:var(--muted);font-size:12px}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{font-size:12px;color:var(--muted);font-weight:600}} .n{{text-align:right;white-space:nowrap}}
.bar{{display:inline-block;width:60px;height:8px;background:var(--soft);border-radius:4px;vertical-align:middle;overflow:hidden}}
.bar span{{display:block;height:100%;background:var(--accent)}}
.caveat{{border-left:3px solid var(--accent);background:var(--soft);padding:8px 12px;margin-top:16px}}
.insights li{{margin-bottom:8px}}
</style></head>
<body><main>
<h1>{escape(log["name"])}</h1>
<p class="meta">Process report · {escape(log["source_format"])} imported {escape(log["imported_at"])} ·
events from {escape(str(summary["first_event_at"] or "–"))} to {escape(str(summary["last_event_at"] or "–"))} ·
generated {escape(data["generated_at"])}</p>
{filter_html}
<div class="tiles">{tile_html}</div>
<p class="caveat">{escape(data["interpretation"])}</p>
{_insight_section(data.get("insights"))}
<h2>Most frequent variants</h2>
<p class="meta">The {len(variants["variants"])} most frequent of {_num(variants["distinct_variants"])} variants cover {coverage} of cases.</p>
<div class="scroll">{_table(["#", "Path", "Cases", "Share", "Median duration"], variant_rows, {0, 2, 3, 4})}</div>
<h2>Activities</h2>
<div class="scroll">{_table(["Activity", "Events", "Cases", "Repeated in cases", "Resources"], activity_rows, {1, 2, 3, 4})}</div>
{_cost_section(data.get("costs"))}
<h2>Slowest transitions</h2>
<p class="meta">Median time between one activity and the next, for transitions seen in at least {_num(slow["minimum_cases"])} cases. This is elapsed time between recorded events, not measured work or verified waiting.</p>
<div class="scroll">{_table(["Transition", "Median gap", "90th percentile", "Cases"], slow_rows, {1, 2, 3})}</div>
<h2>Cases completed per week</h2>
<div class="scroll">{_table(["Week starting", "Cases", "Median throughput"], weekly_rows, {1, 2})}</div>
<p class="meta">Generated by OpenWorkGraph from data stored on this computer. Log {escape(log["log_id"])}.</p>
</main></body></html>
"""


def report_html(log_id: str, case_type: str | None = None, cohort: dict[str, Any] | None = None, *,
                sla_seconds: int | None = None, variant_limit: int = 15) -> str:
    return render(build(log_id, case_type, cohort, sla_seconds=sla_seconds, variant_limit=variant_limit))
