"""Offline, bounded migration assessment using the actual BPMN draft importer.

The report is an inventory of importability, not a saved design, execution
grant, or claim that lowered constructs preserve executable behavior.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from hashlib import sha256
import io
import json
import os
import stat
import sys
from typing import Iterable

from .process_bpmn_import import (
    BPMNImportError, MAX_BYTES, import_bpmn, inspect_bpmn,
)

MAX_FILES = 100
MAX_PROCESSES_PER_FILE = 40
MAX_PROCESS_PREVIEWS = 500
FORMAT = "openworkgraph.bpmn-portfolio-assessment.v1"

REVIEW_COUNTS = (
    "placeholder_labels", "documentation_notes", "lowered_constructs",
    "inlined_subprocesses", "message_flows", "lane_responsibilities",
    "manual_review_conditions", "responsibility_labels", "business_rules",
    "decision_tables", "activities", "transitions",
)
# These are extra migration review flags, not execution or semantic equivalence
# judgments. Even a draft without any extra flags needs human review.
REMEDIATION_COUNTS = (
    "placeholder_labels", "documentation_notes", "lowered_constructs",
    "inlined_subprocesses", "message_flows", "manual_review_conditions",
)
REMEDIATION_WARNINGS = frozenset({
    "typed_task_execution_semantics_not_imported",
    "source_executable_flag_does_not_authorize_execution",
})


def _process_row(ordinal, result):
    model = result["process_model"]
    counts = {
        "placeholder_labels": result["placeholder_label_count"],
        "documentation_notes": len(result["documentation_notes"]),
        "lowered_constructs": len(result["lowered_constructs"]),
        "inlined_subprocesses": len(result["inlined_subprocesses"]),
        "message_flows": result["message_flow_count"],
        "lane_responsibilities": result["lane_responsibility_count"],
        "manual_review_conditions": result["retained_manual_review_condition_count"],
        "responsibility_labels": result["retained_responsibility_label_count"],
        "business_rules": len(model["business_rules"]),
        "decision_tables": len(model["decision_tables"]),
        "activities": len(model["activities"]),
        "transitions": len(model["transitions"]),
    }
    warnings = sorted(set(result["warnings"]))
    remediation = any(counts[key] for key in REMEDIATION_COUNTS) or bool(
        REMEDIATION_WARNINGS.intersection(warnings)
    )
    return {
        "process_ordinal": ordinal,
        "status": "draft_with_remediation" if remediation else "draft_review_required",
        "reason": None,
        "counts": counts,
        "warnings": warnings,
    }


def _blocked(ordinal, status, reason):
    return {"process_ordinal": ordinal, "status": status, "reason": reason,
            "counts": None, "warnings": []}


def _document(ordinal, raw, remaining):
    row = {"file_ordinal": ordinal, "source_sha256": None,
           "status": "not_assessed", "reason": None,
           "process_count": None, "processes": []}
    if raw is None:
        row["reason"] = "Selected file could not be read as a regular file"
        return row, 0
    if not isinstance(raw, bytes):
        raise TypeError("Each selected document must be bytes or None")
    if len(raw) > MAX_BYTES:
        row.update(status="refused", reason="BPMN exceeds the 128 KiB file limit")
        # The caller may have read only a prefix, so never claim an exact hash.
        return row, 0
    row["source_sha256"] = sha256(raw).hexdigest()
    try:
        inspection = inspect_bpmn(raw)
    except BPMNImportError as exc:
        # BPMNImportError's existing contract is fixed safe errors without
        # source XML, IDs, names, or annotation payloads.
        row.update(status="refused", reason=str(exc))
        return row, 0
    choices = inspection["processes"]
    row["process_count"] = len(choices)
    if len(choices) > MAX_PROCESSES_PER_FILE:
        row["reason"] = "Per-file process assessment limit exceeded; split the file"
        return row, 0
    selectable = sum(not choice["blocking_reason"] for choice in choices)
    if selectable > remaining:
        row["reason"] = "Portfolio preview budget exhausted; use a smaller batch"
        return row, 0
    row["status"] = "assessed"
    attempted = 0
    for index, choice in enumerate(choices, 1):
        if choice["blocking_reason"]:
            row["processes"].append(_blocked(index, "refused", choice["blocking_reason"]))
            continue
        attempted += 1
        kwargs = {} if len(choices) == 1 else {
            "process_id": choice["id"],
            "expected_source_sha256": inspection["source_sha256"],
        }
        try:
            draft = import_bpmn(raw, **kwargs)
        except BPMNImportError as exc:
            row["processes"].append(_blocked(index, "refused", str(exc)))
        else:
            if draft["source_sha256"] != row["source_sha256"]:
                raise RuntimeError("BPMN importer returned a mismatched source digest")
            row["processes"].append(_process_row(index, draft))
    return row, attempted


def audit_documents(documents: Iterable[bytes | None]):
    """Inspect selected files in input order, retaining only minimized summaries.

    None represents an unreadable selected file. Oversized input is refused
    without hashing a potentially incomplete prefix. Unexpected implementation
    errors propagate; they are not mislabeled as an unsupported source model.
    """
    files, file_status, process_status = [], Counter(), Counter()
    reasons, warnings, review_counts = Counter(), Counter(), Counter()
    attempted = known_processes = 0
    for ordinal, raw in enumerate(documents, 1):
        if ordinal > MAX_FILES:
            raise ValueError("Select at most 100 files per assessment")
        row, consumed = _document(ordinal, raw, MAX_PROCESS_PREVIEWS - attempted)
        attempted += consumed
        files.append(row)
        file_status[row["status"]] += 1
        if row["reason"]:
            reasons[row["reason"]] += 1
        if row["process_count"] is not None:
            known_processes += row["process_count"]
            if row["status"] == "not_assessed":
                process_status["not_assessed"] += row["process_count"]
        for process in row["processes"]:
            process_status[process["status"]] += 1
            if process["reason"]:
                reasons[process["reason"]] += 1
            warnings.update(process["warnings"])
            if process["counts"] is not None:
                review_counts.update(process["counts"])
    if not files:
        raise ValueError("Select at least one file")
    complete = file_status["not_assessed"] == 0
    refused = file_status["refused"] > 0 or process_status["refused"] > 0
    return {
        "format": FORMAT,
        "limits": {"files": MAX_FILES, "bytes_per_file": MAX_BYTES,
                   "processes_per_file": MAX_PROCESSES_PER_FILE,
                   "process_previews": MAX_PROCESS_PREVIEWS},
        "assessment_complete": complete,
        "all_selected_sources_importable_as_drafts": complete and not refused,
        "execution_authorized": False,
        "process_models_saved": False,
        "summary": {
            "files": len(files),
            "file_status": dict(sorted(file_status.items())),
            "known_processes": known_processes,
            "files_with_unknown_process_count": sum(
                row["process_count"] is None for row in files
            ),
            "process_status": dict(sorted(process_status.items())),
            "attempted_previews": attempted,
            "reason_counts": dict(sorted(reasons.items())),
            "warning_counts": dict(sorted(warnings.items())),
            "draft_review_counts": {key: review_counts[key] for key in REVIEW_COUNTS},
        },
        "files": files,
    }


def _read_file(path):
    """Only explicit regular files; never recurse, read devices or follow feeds."""
    fd = None
    try:
        # Nonblocking avoids hanging on a FIFO swapped in for a regular file on
        # POSIX. The descriptor is checked before reading on every platform.
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as stream:
            fd = None
            return stream.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def audit_paths(paths):
    """No file names, absolute paths, process names or source IDs in the report."""
    return audit_documents(_read_file(path) for path in paths)


def render_csv(report):
    """One row per assessed process or unassessed/refused document.

    JSON is the canonical report; CSV is the review worklist. All text comes
    from fixed importer diagnostics/warning codes, not user-authored XML.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["file_ordinal", "source_sha256", "file_status",
                     "known_file_processes", "process_ordinal", "status",
                     "reason", *REVIEW_COUNTS, "warnings"])
    for file in report["files"]:
        prefix = [file["file_ordinal"], file["source_sha256"], file["status"],
                  file["process_count"]]
        if not file["processes"]:
            writer.writerow([*prefix, None, file["status"], file["reason"],
                             *([None] * len(REVIEW_COUNTS)), None])
            continue
        for process in file["processes"]:
            counts = process["counts"] or {}
            writer.writerow([*prefix, process["process_ordinal"], process["status"],
                             process["reason"], *(counts.get(key) for key in REVIEW_COUNTS),
                             ";".join(process["warnings"])])
    return output.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Explicit UTF-8 BPMN/XML files in review order")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--fail-on-remediation", action="store_true",
                        help="Also return 1 for accepted drafts needing extra remediation")
    args = parser.parse_args(argv)
    if len(args.files) > MAX_FILES:
        parser.error("Select at most 100 files per assessment")
    report = audit_paths(args.files)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        sys.stdout.write(render_csv(report))
    if not report["all_selected_sources_importable_as_drafts"]:
        return 1
    if args.fail_on_remediation and report["summary"]["process_status"].get("draft_with_remediation"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
