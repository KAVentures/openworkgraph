"""Model change: how the discovered process model differs before and after a date.

Concept drift shows *when* the recorded behaviour changed; this shows *how the model* changed, as
ProM's model comparison and Celonis's process comparison views do. Cases are split by when they
started. A model is discovered for each period (the same inductive-miner draft that model discovery
exports), and the two are compared:

* **orderings**: the footprint relation (→, ←, ||, #) of every pair of activities in each model, and
  the pairs whose relation changed;
* **activities** only one period's model has;
* **cross-fit**: how well each period's cases fit the *other* period's model, from optimal
  alignments. If the later cases fit the earlier model much worse than the earlier cases did, the
  way the work is done changed, not just its volume.

Both models are drafts discovered from their own cases. A changed ordering can also come from a
change in how events are recorded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import event_log_alignments as alignments
from . import event_log_footprints as footprints
from . import event_log_model_discovery as discovery
from . import event_log_pnml as pnml
from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_model_change.v1"
MIN_CASES_PER_PERIOD = 10
TOP_CHANGES = 40


def _parse_split(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise EventLogWorkspaceError("split must be an ISO date or time") from None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _relations(tree: dict[str, Any], alphabet: list[str]) -> dict[tuple[str, str], str]:
    fp = footprints._tree_fp(tree)
    follows, par = set(fp.seq), set(fp.par)
    return {(a, b): footprints._relation(a, b, follows, par) for a in alphabet for b in alphabet}


def _compare(log_id: str, case_type: str | None, groups: dict[str, dict[str, Any]], noise: float,
             include_case_evidence: bool):
    """Discover a model per group (two groups, in order) and compare orderings, activities and cross-fit."""
    (first, c1), (second, c2) = groups.items()
    models = {name: discovery.discover(log_id, case_type, noise=noise, cohort=c) for name, c in groups.items()}
    nets = {name: pnml.to_pnml(pnml.tree_to_net(m["tree"]), f"{name} model").encode() for name, m in models.items()}
    acts = {name: footprints._tree_fp(m["tree"]).acts for name, m in models.items()}
    alphabet = sorted(acts[first] | acts[second])
    if len(alphabet) > footprints.MAX_ACTIVITIES:
        raise EventLogWorkspaceError(f"the models show {len(alphabet)} activities; at most "
                                     f"{footprints.MAX_ACTIVITIES} can be compared")
    rel = {name: _relations(m["tree"], alphabet) for name, m in models.items()}
    changes = []
    for a in alphabet:
        for b in alphabet:
            if a > b:
                continue   # each unordered pair once; the reverse relation is implied
            x, y = rel[first][(a, b)], rel[second][(a, b)]
            if x != y:
                changes.append({"from": a, "to": b, first: x, second: y})
    pairs = len(alphabet) * (len(alphabet) + 1) // 2

    def fit(cases_of: str, model_of: str) -> dict[str, Any]:
        r = alignments.alignments(log_id, nets[model_of], case_type, cohort=groups[cases_of])
        result = {"cases": r["cases"], "fitness": r["fitness"]["log"],
                  "fitting_case_share": r["fitness"]["fitting_case_share"]}
        if include_case_evidence:
            result.update({
                "cases_total": r["cases_total"], "cases_excluded": r["cases_excluded"],
                "cases_unaligned": r["unaligned"]["cases"],
                "non_fitting_variants": [
                    {key: variant[key] for key in ("path", "cases", "cost", "fitness")}
                    for variant in r["non_fitting_variants"]
                ],
                "non_fitting_variants_total": r["non_fitting_variants_total"],
            })
        return result

    cross = {f"{first}_cases_on_{first}_model": fit(first, first),
             f"{second}_cases_on_{first}_model": fit(second, first),
             f"{second}_cases_on_{second}_model": fit(second, second),
             f"{first}_cases_on_{second}_model": fit(first, second)}
    return models, acts, changes, pairs, cross


def model_change(log_id: str, case_type: str | None = None, *, split: Any = None, noise: float = 0.2,
                 cohort: dict[str, Any] | None = None, include_case_evidence: bool = False) -> dict[str, Any]:
    if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
        raise EventLogWorkspaceError("noise must be a share from 0 up to (not including) 1")
    base = {k: v for k, v in dict(cohort or {}).items() if v not in (None, "", [], {})}
    if base.get("started_from") or base.get("started_before"):
        raise EventLogWorkspaceError("the periods come from the split date; remove the start-date filter")
    at = _parse_split(split)

    def run(conn):
        starts = conn.execute("SELECT min(ts) FROM ce GROUP BY case_id ORDER BY 1").fetchall()
        return {"starts": [s for (s,) in starts]}

    starts = workspace._query_filtered(log_id, case_type, base, run)["starts"]
    if at is None:
        if len(starts) < 2 * MIN_CASES_PER_PERIOD:
            raise EventLogWorkspaceError(f"at least {2 * MIN_CASES_PER_PERIOD} cases are needed to compare periods")
        at = starts[len(starts) // 2].replace(tzinfo=timezone.utc)
    naive = at.replace(tzinfo=None)
    before_n = sum(1 for s in starts if s < naive)
    after_n = len(starts) - before_n
    if min(before_n, after_n) < MIN_CASES_PER_PERIOD:
        raise EventLogWorkspaceError(f"each period needs at least {MIN_CASES_PER_PERIOD} cases; "
                                     f"this split gives {before_n} before and {after_n} after")
    periods = {"before": {**base, "started_before": _iso(at)}, "after": {**base, "started_from": _iso(at)}}
    models, acts, changes, pairs, cross = _compare(log_id, case_type, periods, noise, include_case_evidence)
    own, moved = cross["before_cases_on_before_model"]["fitness"], cross["after_cases_on_before_model"]["fitness"]
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": models["before"].get("case_notion"),
        "split": _iso(at), "split_chosen": "median case start" if split in (None, "") else "given",
        "noise": float(noise),
        "periods": {"before": {"cases": before_n, "until": _iso(at)}, "after": {"cases": after_n, "from": _iso(at)}},
        "activities": {"only_before": sorted(acts["before"] - acts["after"]),
                       "only_after": sorted(acts["after"] - acts["before"]), "both": len(acts["before"] & acts["after"])},
        "orderings": {"pairs_compared": pairs, "changed": len(changes),
                      "unchanged_share": round(1 - len(changes) / pairs, 4) if pairs else None},
        "changes": changes[:TOP_CHANGES], "changes_total": len(changes),
        "cross_fit": cross,
        **({"case_selections": periods} if include_case_evidence else {}),
        "fitness_drop_on_earlier_model": (round(own - moved, 4) if own is not None and moved is not None else None),
        "models": {name: {"process_model_sha256": m.get("process_model_sha256")} for name, m in models.items()},
        "legend": {"→": "row is followed by column", "←": "column is followed by row", "||": "both orders or parallel",
                   "#": "not directly related"},
        "method": ("cases split by start time; an inductive-miner draft discovered for each period; footprint relations "
                   "compared pair by pair; each period's cases aligned on both models"),
        "caveat": ("Both models are drafts discovered from their own cases. A changed ordering or a drop in fit says the "
                   "recorded behaviour differs between periods, not why; a change in how events are recorded looks the same."),
    }


def _group(raw: Any, what: str) -> dict[str, str]:
    text = str(raw or "").strip()
    name, sep, value = text.partition("=")
    if not sep or not name.strip() or len(text) > 400:
        raise EventLogWorkspaceError(f"{what} must look like attribute=value")
    return {"attribute": f"{name.strip()}={value.strip()}"}


def model_comparison(log_id: str, group_a: Any, group_b: Any, case_type: str | None = None, *,
                     noise: float = 0.2, cohort: dict[str, Any] | None = None, include_case_evidence: bool = False) -> dict[str, Any]:
    """The same comparison between two groups of cases, each selected by a case attribute value
    (for example ``region=EU`` and ``region=US``), as Celonis compares org units."""
    if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
        raise EventLogWorkspaceError("noise must be a share from 0 up to (not including) 1")
    base = {k: v for k, v in dict(cohort or {}).items() if v not in (None, "", [], {})}
    if base.get("attribute") or base.get("event_attribute"):
        raise EventLogWorkspaceError("the groups come from attribute values; remove the attribute filter")
    a, b = _group(group_a, "group_a"), _group(group_b, "group_b")
    if a == b:
        raise EventLogWorkspaceError("choose two different groups")

    def run(conn):
        return {"n": conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]}

    # A case attribute (joined table, OCEL object) first; otherwise an event attribute column of the case.
    groups, counts, matched = {}, {}, {}
    for name, spec in (("a", a), ("b", b)):
        chosen = {**base, **spec}
        n = workspace._query_filtered(log_id, case_type, chosen, run)["n"]
        if not n:
            alt = {**base, "event_attribute": spec["attribute"]}
            m = workspace._query_filtered(log_id, case_type, alt, run)["n"]
            if m:
                chosen, n = alt, m
                matched[name] = "event_attribute"
        groups[name], counts[name] = chosen, n
        matched.setdefault(name, "case_attribute")
    if min(counts.values()) < MIN_CASES_PER_PERIOD:
        raise EventLogWorkspaceError(f"each group needs at least {MIN_CASES_PER_PERIOD} cases; "
                                     f"these give {counts['a']} and {counts['b']}")
    models, acts, changes, pairs, cross = _compare(log_id, case_type, groups, noise, include_case_evidence)
    own, other = cross["a_cases_on_a_model"]["fitness"], cross["b_cases_on_a_model"]["fitness"]
    return {
        "format": FORMAT.replace("model_change", "model_comparison"), "log_id": log_id,
        "case_notion": models["a"].get("case_notion"), "noise": float(noise),
        "groups": {"a": {"filter": a["attribute"], "cases": counts["a"], "matched_by": matched["a"]},
                   "b": {"filter": b["attribute"], "cases": counts["b"], "matched_by": matched["b"]}},
        "activities": {"only_a": sorted(acts["a"] - acts["b"]), "only_b": sorted(acts["b"] - acts["a"]),
                       "both": len(acts["a"] & acts["b"])},
        "orderings": {"pairs_compared": pairs, "changed": len(changes),
                      "unchanged_share": round(1 - len(changes) / pairs, 4) if pairs else None},
        "differences": changes[:TOP_CHANGES], "differences_total": len(changes),
        "cross_fit": cross,
        **({"case_selections": groups} if include_case_evidence else {}),
        "fitness_gap_on_a_model": (round(own - other, 4) if own is not None and other is not None else None),
        "models": {name: {"process_model_sha256": m.get("process_model_sha256")} for name, m in models.items()},
        "legend": {"→": "row is followed by column", "←": "column is followed by row", "||": "both orders or parallel",
                   "#": "not directly related"},
        "method": ("cases grouped by a case attribute value; an inductive-miner draft discovered for each group; "
                   "footprint relations compared pair by pair; each group's cases aligned on both models"),
        "caveat": ("Both models are drafts discovered from their own cases. Differences say the groups' recorded work "
                   "differs, not why; groups can also differ in case mix and in how their systems record events."),
    }


__all__ = ["model_change", "model_comparison", "FORMAT"]
