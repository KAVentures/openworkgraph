"""Read-only readiness gate combining existing M365 evidence and checkpoint."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from .m365_outlook_delta import _scope, _grant, _private_read
from .m365_outlook_checkpoint import inspect_checkpoint
from .m365_outlook_owg_receipt import ReceiptError, _unique

FORMAT = "openworkgraph.m365-sync-readiness.v1"


def readiness(*, tenant, mailbox, folder, expected_address, grant_file: Path,
              identity_report: Path, folder_report: Path, ledger_dir: Path,
              log_id, consent_local_read=False, expected_generation=None):
    if consent_local_read is not True:
        raise ReceiptError("Explicit local evidence read consent required")
    scope = _scope(tenant, mailbox, folder)
    _grant(Path(grant_file), tenant, mailbox, folder)
    def load(path):
        try:
            return json.loads(_private_read(Path(path), 8192), object_pairs_hook=_unique)
        except (ValueError, UnicodeError, RecursionError, TypeError):
            raise ReceiptError("Invalid private preflight report") from None
    identity = load(identity_report)
    folder_data = load(folder_report)
    if (type(identity) is not dict
            or identity.get("format") != "openworkgraph.m365-mailbox-identity.v1"
            or identity.get("status") != "mailbox_identity_observed"
            or identity.get("scope_sha256") != scope
            or identity.get("expected_address_sha256") !=
                hashlib.sha256(expected_address.casefold().encode()).hexdigest()
            or identity.get("mailbox_identifier_matched") is not True
            or not (identity.get("upn_matched") is True or identity.get("mail_matched") is True)
            or identity.get("checkpoint_advanced") is not False
            or type(folder_data) is not dict
            or folder_data.get("format") != "openworkgraph.m365-folder-preflight.v1"
            or folder_data.get("status") != "folder_metadata_observed"
            or folder_data.get("scope_sha256") != scope
            or folder_data.get("folder_id_sha256") != hashlib.sha256(folder.encode()).hexdigest()
            or folder_data.get("folder_id_matched") is not True
            or folder_data.get("checkpoint_advanced") is not False):
        raise ReceiptError("Preflight evidence mismatch")
    entry = inspect_checkpoint(tenant=tenant, mailbox=mailbox, folder=folder,
                               grant_file=Path(grant_file), ledger_dir=Path(ledger_dir),
                               consent_local_read=True)
    if entry is None or entry.get("log_id") != log_id:
        raise ReceiptError("No committed checkpoint for requested log")
    # A reviewed operator decision is bound to a specific committed generation.
    # Reject stale review when another operator advances the ledger in between.
    if expected_generation is not None:
        if (type(expected_generation) is not int or expected_generation < 1
                or entry.get("generation") != expected_generation):
            raise ReceiptError("Committed checkpoint generation changed")
    _grant(Path(grant_file), tenant, mailbox, folder)
    return {"format": FORMAT, "status": "operator_review_ready",
            "committed_generation": entry["generation"],
            "mailbox_identity_observed": True, "folder_metadata_observed": True,
            "tenant_identity_verified": False, "provider_requests": 0,
            "checkpoint_advanced": False, "automatic_sync": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "expected-address", "log-id"):
        p.add_argument("--" + key, required=True)
    for key in ("grant-file", "identity-report", "folder-report", "ledger-dir"):
        p.add_argument("--" + key, required=True, type=Path)
    p.add_argument("--consent-local-read", action="store_true")
    p.add_argument("--expected-generation", type=int)
    a = vars(p.parse_args(argv))
    try:
        print(json.dumps(readiness(**a), sort_keys=True))
        return 0
    except (ReceiptError, ValueError, TypeError, OSError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
