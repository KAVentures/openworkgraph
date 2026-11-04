"""Opt-in, bounded live Microsoft Graph folder metadata preflight (read only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib import error, parse, request

from .m365_outlook_delta import _scope, _grant, _delta_private_parent
from .m365_outlook_selected import _TOKEN, _opener, _pairs
from .m365_outlook_verified_append_receipt import _exclusive_private

FORMAT = "openworkgraph.m365-folder-preflight.v1"
MAX_BYTES = 8192


class PreflightError(ValueError):
    """Minimized error without provider identifiers or secrets."""


def _parse_folder(raw: bytes, folder_id: str):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise PreflightError("Invalid folder metadata size")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise PreflightError("Invalid folder metadata") from None
    if (type(value) is not dict or value.get("id") != folder_id
            or type(value.get("displayName")) is not str
            or len(value["displayName"]) > 256
            or type(value.get("childFolderCount")) is not int
            or not 0 <= value["childFolderCount"] <= 100000000
            or type(value.get("totalItemCount")) is not int
            or not 0 <= value["totalItemCount"] <= 100000000):
        raise PreflightError("Folder identity or metadata mismatch")
    return value["childFolderCount"], value["totalItemCount"]


def preflight(*, tenant, mailbox, folder, grant_file: Path, output: Path,
              token, consent_graph_read=False, consent_private_report=False,
              opener=None):
    if consent_graph_read is not True or consent_private_report is not True:
        raise PreflightError("Separate Graph read and private report consent required")
    scope = _scope(tenant, mailbox, folder)
    if type(token) is not str or not 1 <= len(token) <= 4096 or not _TOKEN.fullmatch(token):
        raise PreflightError("Invalid bearer token")
    grant_file, output = Path(grant_file), Path(output)
    _delta_private_parent(output.parent)
    if (output.suffix != ".json" or not output.name.startswith("folder-preflight-")
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for c in output.name)
            or output == grant_file or output.exists() or output.is_symlink()):
        raise PreflightError("Unsafe private report destination")
    _grant(grant_file, tenant, mailbox, folder)
    url = ("https://graph.microsoft.com/v1.0/me/mailFolders/"
           + parse.quote(folder, safe="")
           + "?%24select=id,displayName,childFolderCount,totalItemCount")
    req = request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    })
    transport = _opener() if opener is None else opener
    try:
        with transport.open(req, timeout=15) as response:
            if (response.status != 200 or response.geturl() != url
                    or not response.headers.get("Content-Type", "").lower().startswith("application/json")):
                raise PreflightError("Unexpected Graph response")
            raw = response.read(MAX_BYTES + 1)
    except (OSError, error.URLError, ValueError) as exc:
        if isinstance(exc, PreflightError):
            raise
        raise PreflightError("Graph folder read unavailable") from None
    children, total = _parse_folder(raw, folder)
    _grant(grant_file, tenant, mailbox, folder)
    report = {
        "format": FORMAT, "status": "folder_metadata_observed",
        "scope_sha256": scope,
        "folder_id_sha256": hashlib.sha256(folder.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "child_folder_count": children, "total_item_count": total,
        "folder_id_matched": True, "provider_tenant_verified": False,
        "mailbox_identity_verified": False, "delta_sync_performed": False,
        "owg_import_performed": False, "checkpoint_advanced": False,
        "safe_to_retry": False,
    }
    _grant(grant_file, tenant, mailbox, folder)
    try:
        _exclusive_private(output, (json.dumps(report, sort_keys=True) + "\n").encode())
    except FileExistsError:
        raise PreflightError("Report already exists") from None
    return {"format": FORMAT, "status": report["status"],
            "folder_id_matched": True, "safe_to_retry": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder"):
        p.add_argument("--" + key, required=True)
    for key in ("grant-file", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--consent-graph-read", action="store_true")
    p.add_argument("--consent-private-report", action="store_true")
    a = p.parse_args(argv)
    try:
        result = preflight(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            grant_file=a.grant_file, output=a.output,
            token=os.environ.get("OWG_M365_GRAPH_TOKEN"),
            consent_graph_read=a.consent_graph_read,
            consent_private_report=a.consent_private_report)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PreflightError, ValueError, TypeError, OSError):
        print(json.dumps({"format": FORMAT, "status": "refused"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
