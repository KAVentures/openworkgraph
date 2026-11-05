"""First Microsoft 365 -> OWG operator cycle, with independent review between phases.

The extract phase performs real, bounded Graph GETs through the existing verified
reader. The append phase uses the existing authenticated local OWG append and
durable uncertain-write latch. Neither phase advances a provider checkpoint.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .m365_outlook_verified_delta_round import verified_delta_round
from .m365_outlook_verified_append_receipt import append_and_record
from .m365_outlook_owg_receipt import ReceiptError, UncertainReceipt
from .m365_outlook_delta import DeltaError

FORMAT = "openworkgraph.m365-first-operator-cycle.v1"


def extract_first(*, consent_graph_read=False, consent_private_reports=False,
                  consent_delta_export=False, **kwargs):
    if not (consent_graph_read is True and consent_private_reports is True
            and consent_delta_export is True):
        raise ReceiptError("Independent provider and private artifact consents required")
    if kwargs.get("previous_candidate") is not None or kwargs.get("confirm_previous_import"):
        raise ReceiptError("First cycle must not resume an existing cursor")
    result = verified_delta_round(
        consent_graph_read=True, consent_private_reports=True,
        consent_delta_export=True, **kwargs)
    if (result.get("status") != "verified_export_review_ready"
            or result.get("complete_delta_round") is not True
            or result.get("checkpoint_advanced") is not False
            or result.get("automatic_owg_append") is not False):
        raise ReceiptError("Verified first-round extraction incomplete")
    return {
        "format": FORMAT, "phase": "extract", "status": "review_required",
        "tenant_id_verified": result.get("tenant_id_verified") is True,
        "event_snapshots": result["event_snapshots"],
        "removal_markers_for_review": result["removal_markers_for_review"],
        "checkpoint_advanced": False, "owg_append_performed": False,
    }


def append_reviewed(*, consent_local_read=False, consent_local_append=False,
                    confirm_candidate_reviewed=False, **kwargs):
    if not (consent_local_read is True and consent_local_append is True
            and confirm_candidate_reviewed is True):
        raise ReceiptError("Independent local read, append and review confirmation required")
    result = append_and_record(
        consent_local_read=True, consent_local_append=True, **kwargs)
    if (result.get("status") != "receipt_persisted"
            or result.get("checkpoint_advanced") is not False
            or result.get("automatic_retry") is not False):
        raise UncertainReceipt("OWG append result uncertain; never retry blindly")
    return {
        "format": FORMAT, "phase": "append", "status": "receipt_persisted",
        "log_id": result["log_id"], "events_after": result["events_after"],
        "checkpoint_advanced": False, "automatic_retry": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("extract", "append"):
        p = sub.add_parser(phase)
        for key in ("tenant", "mailbox", "folder"):
            p.add_argument("--" + key, required=True)
        p.add_argument("--grant-file", type=Path, required=True)
        if phase == "extract":
            p.add_argument("--expected-address", required=True)
            for key in ("identity-report", "folder-report", "csv-output", "candidate-output"):
                p.add_argument("--" + key, type=Path, required=True)
            p.add_argument("--expected-tenant-id")
            p.add_argument("--tenant-report", type=Path)
            for flag in ("consent-graph-read", "consent-private-reports",
                         "consent-delta-export", "consent-tenant-lookup"):
                p.add_argument("--" + flag, action="store_true")
        else:
            for key in ("candidate", "csv-path", "receipt-file"):
                p.add_argument("--" + key, type=Path, required=True)
            for key in ("log-id", "confirm-log-id"):
                p.add_argument("--" + key, required=True)
            p.add_argument("--port", type=int, default=8765)
            for flag in ("consent-local-read", "consent-local-append",
                         "confirm-candidate-reviewed"):
                p.add_argument("--" + flag, action="store_true")
    args = vars(parser.parse_args(argv))
    phase = args.pop("phase")
    try:
        if phase == "extract":
            args["token"] = os.environ.get("OWG_M365_GRAPH_TOKEN")
            result = extract_first(**args)
        else:
            args["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
            result = append_reviewed(**args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainReceipt:
        print(json.dumps({"format": FORMAT, "phase": phase, "status": "uncertain",
                          "retry_allowed": False}, sort_keys=True))
        return 3
    except (ReceiptError, DeltaError, ValueError, TypeError, OSError, KeyError):
        print(json.dumps({"format": FORMAT, "phase": phase, "status": "refused",
                          "retry_allowed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
