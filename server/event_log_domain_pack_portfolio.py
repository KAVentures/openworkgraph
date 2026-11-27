"""Local multi-log portfolio benchmarking over installed, role-aligned packs.

A reviewed baseline is compared with every selected log on the same pack
version and parameters. Logs can overlap: never pool case counts or infer
site rankings. Existing pairwise engine semantics are preserved.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

from . import event_log_domain_pack_benchmark as benchmark
from . import event_log_domain_packs as packs
from . import event_log_workspace as workspace
from . import process_discovery_provenance as provenance

FORMAT = "openworkgraph.domain-pack-portfolio.v1"
MAX_LOGS = 8
MAX_SOURCE_ROWS = 750_000
SECTIONS = ("metrics", "compliance", "object_rules")


def _fail(message):
    raise workspace.EventLogWorkspaceError(message)


def _parse(log_id, spec: Any, compare=False):
    required = {"other_log_ids"} | ({"expected_preflight_sha256"} if compare else set())
    if not isinstance(spec, dict) or set(spec) != required:
        _fail("Choose comparison logs and review their exact installations")
    others = spec["other_log_ids"]
    if not isinstance(others, list) or not 2 <= len(others) < MAX_LOGS:
        _fail("Choose two to seven comparison logs")
    if any(not isinstance(x, str) or not 1 <= len(x) <= 200 for x in others):
        _fail("Invalid comparison log identifier")
    if log_id in others or len(set(others)) != len(others):
        _fail("Choose distinct retained logs, excluding the baseline")
    if compare:
        pin = spec["expected_preflight_sha256"]
        if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
            _fail("Invalid reviewed portfolio digest")
    return [log_id, *sorted(others)]


def _prepare(ids, pack_id):
    conn = workspace._connect()
    try:
        # Validate all selections before hashing any of them.
        for log in ids:
            workspace._check_log(conn, log)
        counts = {log: provenance.source_rows(conn, log) for log in ids}
        if sum(counts.values()) > MAX_SOURCE_ROWS:
            _fail("Combined sources exceed the complete portfolio source-row budget")
        prepared = [benchmark._basis(conn, log, pack_id) for log in ids]
    finally:
        conn.close()
    sources = [{**item[0], "source_rows": counts[log]} for log, item in zip(ids, prepared)]
    definition = prepared[0][1]
    baseline = sources[0]
    blockers = []
    for source, (_, declared, _) in zip(sources[1:], prepared[1:]):
        reasons = []
        if source["version"] != baseline["version"]:
            reasons.append("different_pack_versions")
        elif provenance.digest(declared) != provenance.digest(definition):
            reasons.append("different_catalog_definitions")
        if source["parameters"] != baseline["parameters"]:
            reasons.append("different_parameter_values")
        if reasons:
            blockers.append({"log_id": source["log_id"], "reasons": reasons})
    roles = []
    for kind in ("object_types", "activities"):
        for role in sorted({key for s in sources for key in s["mapping"][kind]}):
            labels = [s["mapping"][kind].get(role) for s in sources]
            roles.append({"kind": kind, "role": role, "labels": labels,
                          "mapped_everywhere": all(label is not None for label in labels)})
    # Equal complete fingerprints can flag duplicate content. Different
    # fingerprints cannot establish disjoint populations or independent units.
    identical_pairs = [{"left_log_id": a["log_id"], "right_log_id": b["log_id"]}
                       for a, b in combinations(sources, 2)
                       if a["source_sha256"] == b["source_sha256"]]
    result = {"format": FORMAT, "pack_id": pack_id, "baseline_log_id": ids[0],
              "sources": sources, "source_rows": sum(counts.values()),
              "catalog_sha256": provenance.digest(definition),
              "role_alignment": roles, "blockers": blockers,
              "ready_to_compare": not blockers, "identical_content_pairs": identical_pairs,
              "disjoint_populations_verified": False}
    result["preflight_sha256"] = provenance.digest(result)
    return result, definition, [item[2] for item in prepared]


def preflight(log_id, pack_id, spec):
    ids = _parse(log_id, spec)
    # Share the guard with the original benchmark rather than competing for
    # the workspace lock during long report runs.
    if not benchmark._GUARD.acquire(blocking=False):
        _fail("Another domain benchmark is running")
    try:
        with workspace._LOCK:
            snapshot, _, _ = _prepare(ids, pack_id)
        return snapshot
    finally:
        benchmark._GUARD.release()


def _section_slots(definition, resolved, report):
    return {"metrics": benchmark._metric_slots(definition, resolved, report),
            "compliance": benchmark._compliance_slots(definition, resolved, report),
            "object_rules": benchmark._object_slots(definition, resolved, report)}


def _matrix(definition, section, slots, ids):
    """Align canonical definitions without repeating source reports or values."""
    comparisons = [benchmark._compare_slots(definition, section, slots[0], other)
                   for other in slots[1:]]
    rows = []
    for index, declared in enumerate(definition[section]):
        values = [slot.get(index) for slot in slots]
        deltas = []
        for log, comp in zip(ids[1:], comparisons):
            row = comp[index]
            deltas.append({"log_id": log, "comparable": row["comparable"],
                           "not_comparable_reason": row["not_comparable_reason"],
                           "changes": row["changes"]})
        # A baseline-relative comparable flag is not transitive when the
        # baseline is unmapped or lacks observations. Never infer comparisons
        # between other sources from these flags.
        rows.append({"definition_index": index, "canonical_definition": declared,
                     "values": values, "baseline_comparisons": deltas,
                     "mapped_source_count": sum(v is not None for v in values),
                     "comparable_to_baseline_count": sum(d["comparable"] for d in deltas)})
    return rows


def compare(log_id, pack_id, spec):
    ids = _parse(log_id, spec, compare=True)
    if not benchmark._GUARD.acquire(blocking=False):
        _fail("Another domain benchmark is running")
    try:
        with workspace._LOCK:
            snapshot, definition, resolved = _prepare(ids, pack_id)
            if snapshot["preflight_sha256"] != spec["expected_preflight_sha256"]:
                _fail("A reviewed installation or retained source changed; review the portfolio again")
            if not snapshot["ready_to_compare"]:
                _fail("All selected installations must use the same pack version and parameters")
            # Each selected report is computed exactly once, not once per
            # baseline comparison. No raw case examples enter the response.
            slots, models = [], []
            for log, config in zip(ids, resolved):
                report = packs.report(log, pack_id)
                slots.append(_section_slots(definition, config, report))
                models.append({"log_id": log, "model": benchmark._model_summary(report)})
            matrices = {section: _matrix(definition, section, [s[section] for s in slots], ids)
                        for section in SECTIONS}
            result = {**snapshot, **matrices, "object_models": models,
                      "source_reports_computed": len(ids),
                      "raw_case_identifiers_returned": False,
                      "pooled_populations_reported": False, "source_completeness_verified": False,
                      "causal_effect_estimated": False, "policy_compliance_verified": False,
                      "execution_authorized": False,
                      "interpretation": [
                          "Every delta is comparison minus baseline, aligned by canonical pack slot. Identical labels are not required and semantic equivalence is not independently verified.",
                          "Unmapped rules, no observed values, no activated units and incomplete computations remain unavailable; they are not zeroes.",
                          "Case-rule point rates use decided activated cases. Bounds include unknown activated cases.",
                          "Different logs may share events or cases. Equal fingerprints flag identical content; unequal fingerprints do not prove disjoint populations.",
                          "No pooled counts, rankings, causal effect, business savings or legal compliance are inferred.",
                          "Fingerprints cover stored events, objects and event-object links, not temporal object history or supplied relationships; existing benchmark scope restrictions apply."]}
            result["report_sha256"] = provenance.digest(result)
            return result
    finally:
        benchmark._GUARD.release()
