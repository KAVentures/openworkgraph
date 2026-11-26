"""Compare explicitly paired BPMN migration sources before and after remediation.

Both sides are freshly assessed by the actual importer. File pairing is
caller-declared; changed documents are never matched by process ordinal or
treated as semantically equivalent. No source paths or process data leave
the assessment unless the operator redirects the minimized report.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import io
import json
import sys

from .process_bpmn_portfolio import MAX_FILES, audit_paths, audit_documents

FORMAT = "openworkgraph.bpmn-migration-comparison.v1"
STATUSES = ("draft_review_required", "draft_with_remediation", "refused")
COUNT_FIELDS = (*STATUSES, "not_assessed")


def _counts(file):
    if file["status"] != "assessed":
        return None
    counts = Counter(process["status"] for process in file["processes"])
    return {status: counts[status] for status in COUNT_FIELDS}


def _source(file):
    return {
        "source_sha256": file["source_sha256"],
        "file_status": file["status"],
        "known_processes": file["process_count"],
        "process_status_counts": _counts(file),
    }


def _readiness(file):
    if file["status"] == "not_assessed":
        return "unknown"
    if file["status"] == "refused":
        return "document_refused"
    counts = _counts(file)
    if counts["refused"]:
        return "process_refused"
    if counts["draft_with_remediation"]:
        return "extra_remediation"
    return "draft_review_required"


def _compare_pair(before, after):
    old, new = _source(before), _source(after)
    left, right = _readiness(before), _readiness(after)
    both_digests = bool(old["source_sha256"] and new["source_sha256"])
    same = (old["source_sha256"] == new["source_sha256"]) if both_digests else None
    old_n, new_n = old["known_processes"], new["known_processes"]
    population_change = new_n - old_n if old_n is not None and new_n is not None else None
    deltas = None
    if old["process_status_counts"] is not None and new["process_status_counts"] is not None:
        deltas = {status: new["process_status_counts"][status] - old["process_status_counts"][status]
                  for status in COUNT_FIELDS}
    if left == "unknown" or right == "unknown":
        change = "not_comparable"
    elif same is True and (left != right or deltas and any(deltas.values()) or population_change):
        change = "same_source_assessment_mismatch"
    elif population_change:
        change = "process_population_changed"
    elif left == right:
        change = "same_readiness_category"
    else:
        # These are file-level readiness transitions, not matched process
        # outcomes. A changed document may have replaced every process.
        change = "readiness_category_changed"
    return {
        "pair_ordinal": before["file_ordinal"],
        "before": old,
        "after": new,
        "before_readiness": left,
        "after_readiness": right,
        "same_source_bytes": same,
        "known_process_population_delta": population_change,
        "process_status_count_deltas": deltas,
        "change": change,
        "process_identity_matched": False,
        "semantic_equivalence_verified": False,
    }


def compare_assessments(before, after):
    """Combine assessments from the current run, not arbitrary saved reports."""
    if len(before["files"]) != len(after["files"]):
        raise ValueError("Every before source needs exactly one explicitly paired after source")
    if not before["files"]:
        raise ValueError("Select at least one source pair")
    pairs = []
    transitions = Counter()
    changes = Counter()
    for old, new in zip(before["files"], after["files"]):
        if old["file_ordinal"] != new["file_ordinal"]:
            raise ValueError("Pair ordinals do not agree")
        pair = _compare_pair(old, new)
        pairs.append(pair)
        transitions[(pair["before_readiness"], pair["after_readiness"])] += 1
        changes[pair["change"]] += 1
    return {
        "format": FORMAT,
        "pairing_basis": "explicit_caller_file_pairs",
        "before_assessment_complete": before["assessment_complete"],
        "after_assessment_complete": after["assessment_complete"],
        "all_after_sources_importable_as_drafts": after["all_selected_sources_importable_as_drafts"],
        "semantic_equivalence_verified": False,
        "execution_authorized": False,
        "process_models_saved": False,
        "summary": {
            "pairs": len(pairs),
            "changes": dict(sorted(changes.items())),
            "readiness_transitions": [
                {"before": old, "after": new, "pairs": count}
                for (old, new), count in sorted(transitions.items())
            ],
            "before": before["summary"],
            "after": after["summary"],
        },
        "pairs": pairs,
    }


def compare_documents(pairs):
    before, after = [], []
    for old, new in pairs:
        if len(before) >= MAX_FILES:
            raise ValueError("Select at most 100 source pairs")
        before.append(old)
        after.append(new)
    return compare_assessments(audit_documents(before), audit_documents(after))


def compare_paths(pairs):
    before, after = [], []
    for old, new in pairs:
        if len(before) >= MAX_FILES:
            raise ValueError("Select at most 100 source pairs")
        before.append(old)
        after.append(new)
    return compare_assessments(audit_paths(before), audit_paths(after))


def render_csv(report):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "pair_ordinal", "before_sha256", "after_sha256", "same_source_bytes",
        "before_readiness", "after_readiness", "change",
        "before_known_processes", "after_known_processes", "known_process_population_delta",
        *(side + "_" + field for side in ("before", "after", "delta") for field in COUNT_FIELDS),
    ])
    for pair in report["pairs"]:
        before, after = pair["before"], pair["after"]
        writer.writerow([
            pair["pair_ordinal"], before["source_sha256"], after["source_sha256"],
            pair["same_source_bytes"], pair["before_readiness"], pair["after_readiness"],
            pair["change"], before["known_processes"], after["known_processes"],
            pair["known_process_population_delta"],
            *((counts or {}).get(field)
              for counts in (before["process_status_counts"], after["process_status_counts"],
                             pair["process_status_count_deltas"])
              for field in COUNT_FIELDS),
        ])
    return output.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", nargs=2, action="append", required=True,
                        metavar=("BEFORE", "AFTER"), help="Explicitly paired source files; repeat")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--fail-on-population-change", action="store_true",
                        help="Also refuse success when a process count changed or is unknown")
    args = parser.parse_args(argv)
    if len(args.pair) > MAX_FILES:
        parser.error("Select at most 100 source pairs")
    report = compare_paths(args.pair)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        sys.stdout.write(render_csv(report))
    if not (report["before_assessment_complete"] and report["all_after_sources_importable_as_drafts"]):
        return 1
    if report["summary"]["changes"].get("same_source_assessment_mismatch"):
        return 1
    if args.fail_on_population_change and any(
        pair["known_process_population_delta"] != 0 for pair in report["pairs"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
