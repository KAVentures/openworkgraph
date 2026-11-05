"""Consent-gated live mailbox+folder verified Microsoft Graph delta export.

This composes existing real-provider readers; it does not import or commit a cursor.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .m365_outlook_delta import _grant, _delta_private_parent, export_delta
from .m365_outlook_folder_preflight import preflight as folder_preflight
from .m365_outlook_mailbox_identity import preflight as mailbox_preflight
from .m365_outlook_tenant_preflight import preflight as tenant_preflight

FORMAT = "openworkgraph.m365-verified-delta-round.v1"


class VerifiedDeltaError(ValueError):
    """Generic refusal, without exposing mailbox, provider data or credentials."""


def verified_delta_round(
    *, tenant, mailbox, folder, expected_address, grant_file: Path,
    identity_report: Path, folder_report: Path, csv_output: Path,
    candidate_output: Path, token, consent_graph_read=False,
    consent_private_reports=False, consent_delta_export=False,
    previous_candidate: Path | None = None, confirm_previous_import=False,
    opener=None, expected_tenant_id=None, tenant_report: Path | None = None,
    consent_tenant_lookup=False,
):
    """Read /me and exact folder before a bounded delta round, with no OWG writes.

    A failed later stage may leave earlier private reports or CSV artifacts.
    Those artifacts are evidence only, never permission to retry or advance.
    """
    if (consent_graph_read is not True or consent_private_reports is not True
            or consent_delta_export is not True):
        raise VerifiedDeltaError("Separate live read, private report and export consent required")
    grant_file = Path(grant_file)
    identity_report = Path(identity_report)
    folder_report = Path(folder_report)
    csv_output = Path(csv_output)
    candidate_output = Path(candidate_output)
    if (expected_tenant_id is None) != (tenant_report is None):
        raise VerifiedDeltaError("Tenant ID and private report must be provided together")
    if expected_tenant_id is not None and consent_tenant_lookup is not True:
        raise VerifiedDeltaError("Separate tenant lookup consent required")
    outputs = (identity_report, folder_report, csv_output, candidate_output)
    if tenant_report is not None:
        outputs += (Path(tenant_report),)
    if (len({str(p.absolute()) for p in outputs + (grant_file,)}) != len(outputs) + 1
            or len({p.parent.absolute() for p in outputs}) != 1
            or any(p.exists() or p.is_symlink() for p in outputs)):
        raise VerifiedDeltaError("Unsafe or existing output paths")
    _delta_private_parent(csv_output.parent)
    _grant(grant_file, tenant, mailbox, folder)
    # Use the same transport for every provider read; each component rejects redirects,
    # unsafe responses and grant revocation independently.
    tenant_verified = False
    if expected_tenant_id is not None:
        tenant_result = tenant_preflight(
            tenant=tenant, mailbox=mailbox, folder=folder,
            expected_tenant_id=expected_tenant_id, grant_file=grant_file,
            output=Path(tenant_report), token=token,
            consent_graph_read=True, consent_private_report=True,
            consent_tenant_lookup=True, opener=opener)
        if tenant_result.get("tenant_id_verified") is not True:
            raise VerifiedDeltaError("Graph tenant identity not verified")
        tenant_verified = True
        _grant(grant_file, tenant, mailbox, folder)
    mailbox_result = mailbox_preflight(
        tenant=tenant, mailbox=mailbox, folder=folder,
        expected_address=expected_address, grant_file=grant_file,
        output=identity_report, token=token, consent_graph_read=True,
        consent_private_report=True, opener=opener)
    if mailbox_result.get("mailbox_identifier_matched") is not True:
        raise VerifiedDeltaError("Mailbox identity not verified")
    _grant(grant_file, tenant, mailbox, folder)
    folder_result = folder_preflight(
        tenant=tenant, mailbox=mailbox, folder=folder,
        grant_file=grant_file, output=folder_report, token=token,
        consent_graph_read=True, consent_private_report=True, opener=opener)
    if folder_result.get("folder_id_matched") is not True:
        raise VerifiedDeltaError("Folder identity not verified")
    _grant(grant_file, tenant, mailbox, folder)
    delta = export_delta(
        tenant=tenant, mailbox=mailbox, folder_id=folder,
        grant_file=grant_file, output=csv_output, candidate=candidate_output,
        token=token, consent_read=True, consent_export=True,
        previous_candidate=previous_candidate,
        confirm_previous_import=confirm_previous_import, opener=opener)
    if delta.get("complete_delta_round") is not True:
        raise VerifiedDeltaError("Incomplete delta round")
    return {
        "format": FORMAT, "status": "verified_export_review_ready",
        "mailbox_identifier_matched": True, "folder_id_matched": True,
        "complete_delta_round": True,
        "event_snapshots": delta["event_snapshots"],
        "removal_markers_for_review": delta["removal_markers_for_review"],
        "provider_get_requests_delta": delta["provider_get_requests"],
        "automatic_owg_append": False, "checkpoint_advanced": False,
        "tenant_id_verified": tenant_verified, "safe_to_retry": False,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant", "mailbox", "folder", "expected-address"):
        p.add_argument("--" + name, required=True)
    for name in ("grant-file", "identity-report", "folder-report",
                 "csv-output", "candidate-output"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--expected-tenant-id")
    p.add_argument("--tenant-report", type=Path)
    p.add_argument("--consent-tenant-lookup", action="store_true")
    p.add_argument("--previous-candidate", type=Path)
    p.add_argument("--confirm-previous-import", action="store_true")
    p.add_argument("--consent-graph-read", action="store_true")
    p.add_argument("--consent-private-reports", action="store_true")
    p.add_argument("--consent-delta-export", action="store_true")
    a = p.parse_args(argv)
    try:
        result = verified_delta_round(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            expected_address=a.expected_address, grant_file=a.grant_file,
            identity_report=a.identity_report, folder_report=a.folder_report,
            csv_output=a.csv_output, candidate_output=a.candidate_output,
            previous_candidate=a.previous_candidate,
            confirm_previous_import=a.confirm_previous_import,
            consent_graph_read=a.consent_graph_read,
            consent_private_reports=a.consent_private_reports,
            consent_delta_export=a.consent_delta_export,
            token=os.environ.get("OWG_M365_GRAPH_TOKEN"),
            expected_tenant_id=a.expected_tenant_id,
            tenant_report=a.tenant_report,
            consent_tenant_lookup=a.consent_tenant_lookup)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, TypeError, OSError, KeyError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "partial_private_artifacts_possible": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
