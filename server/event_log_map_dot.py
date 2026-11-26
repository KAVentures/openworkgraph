"""Export the simplified process map as a Graphviz DOT graph.

Graphviz is what PM4Py, bupaR and many notebooks use to draw directly-follows graphs, and DOT files
open in Graphviz Online, VS Code previewers, Gephi and yEd. The export draws the same map as
``event_log_process_map`` at the same slider settings: a start and an end node, one box per shown
activity labelled with its event count, and one arrow per shown transition labelled with its
count and median elapsed gap. Arrow width grows with frequency, as on the dashboard map.

Node identifiers are generated (``a0``, ``a1``, …); activity names only appear inside quoted,
escaped labels, so a name can never inject DOT syntax.
"""
from __future__ import annotations

from typing import Any


def _quote(text: str) -> str:
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", "\\n"))
    return f'"{escaped}"'


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.1f}{unit}"
    return f"{seconds:.0f}s"


def _width(count: int, most: int) -> str:
    return f"{1 + 5 * count / most:.2f}" if most else "1"


def to_dot(result: dict[str, Any], name: str) -> str:
    ids = {a["activity"]: f"a{i}" for i, a in enumerate(result["activities"])}
    counts = ([t["transition_count"] for t in result["transitions"]]
              + list(result["start_activities"].values()) + list(result["end_activities"].values()))
    most = max(counts, default=0)
    lines = [f"digraph {_quote(name)} {{", "  rankdir=TB;",
             '  node [shape=box, style="rounded,filled", fillcolor="#eef3fb", fontname="Helvetica"];',
             '  edge [fontname="Helvetica", fontsize=10];',
             '  start [shape=circle, label="", width=0.3, fillcolor="#3a7d44"];',
             '  end [shape=doublecircle, label="", width=0.25, fillcolor="#a33"];']
    for a in result["activities"]:
        lines.append(f"  {ids[a['activity']]} [label={_quote(a['activity'] + chr(10) + str(a['event_count']))}];")
    for activity, n in result["start_activities"].items():
        lines.append(f"  start -> {ids[activity]} [label={_quote(str(n))}, penwidth={_width(n, most)}, style=dashed];")
    for t in result["transitions"]:
        label = f"{t['transition_count']} · {_duration(t['median_elapsed_gap_seconds'])}"
        lines.append(f"  {ids[t['from']]} -> {ids[t['to']]} "
                     f"[label={_quote(label)}, penwidth={_width(t['transition_count'], most)}];")
    for activity, n in result["end_activities"].items():
        lines.append(f"  {ids[activity]} -> end [label={_quote(str(n))}, penwidth={_width(n, most)}, style=dashed];")
    lines.append("}")
    return "\n".join(lines) + "\n"


def map_dot(log_id: str, case_type: str | None = None, *, activities: float = 1.0, paths: float = 1.0,
            cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    from . import event_log_process_map as process_maps
    result = process_maps.process_map(log_id, case_type, activities=activities, paths=paths, cohort=cohort)
    meta = {"activities": len(result["activities"]), "transitions": len(result["transitions"])}
    return to_dot(result, f"{result['log']['name']} ({result['case_notion']}) process map"[:200]), meta
