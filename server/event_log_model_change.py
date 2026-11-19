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


def model_change(log_id: str, case_type: str | None = None, *, split: Any = None, noise: float = 0.2,
                 cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
        raise EventLogWorkspaceError("noise must be a share from 0 up to (not including) 1")
    base = dict(cohort or {})
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
    models = {name: discovery.discover(log_id, case_type, noise=noise, cohort=c) for name, c in periods.items()}
    nets = {name: pnml.to_pnml(pnml.tree_to_net(m["tree"]), f"{name} model").encode() for name, m in models.items()}
    acts = {name: footprints._tree_fp(m["tree"]).acts for name, m in models.items()}
    alphabet = sorted(acts["before"] | acts["after"])
    if len(alphabet) > footprints.MAX_ACTIVITIES:
        raise EventLogWorkspaceError(f"the models show {len(alphabet)} activities; at most "
                                     f"{footprints.MAX_ACTIVITIES} can be compared")
    rel = {name: _relations(m["tree"], alphabet) for name, m in models.items()}
    changes = []
    for a in alphabet:
        for b in alphabet:
            if a > b:
                continue   # each unordered pair once; the reverse relation is implied
            old, new = rel["before"][(a, b)], rel["after"][(a, b)]
            if old != new:
                changes.append({"from": a, "to": b, "before": old, "after": new})
    pairs = len(alphabet) * (len(alphabet) + 1) // 2

    def fit(cases_of: str, model_of: str) -> dict[str, Any]:
        r = alignments.alignments(log_id, nets[model_of], case_type, cohort=periods[cases_of])
        return {"cases": r["cases"], "fitness": r["fitness"]["log"],
                "fitting_case_share": r["fitness"]["fitting_case_share"]}

    cross = {"before_cases_on_before_model": fit("before", "before"),
             "after_cases_on_before_model": fit("after", "before"),
             "after_cases_on_after_model": fit("after", "after"),
             "before_cases_on_after_model": fit("before", "after")}
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
        "fitness_drop_on_earlier_model": (round(own - moved, 4) if own is not None and moved is not None else None),
        "models": {name: {"process_model_sha256": m.get("process_model_sha256")} for name, m in models.items()},
        "legend": {"→": "row is followed by column", "←": "column is followed by row", "||": "both orders or parallel",
                   "#": "not directly related"},
        "method": ("cases split by start time; an inductive-miner draft discovered for each period; footprint relations "
                   "compared pair by pair; each period's cases aligned on both models"),
        "caveat": ("Both models are drafts discovered from their own cases. A changed ordering or a drop in fit says the "
                   "recorded behaviour differs between periods, not why; a change in how events are recorded looks the same."),
    }


__all__ = ["model_change", "FORMAT"]
