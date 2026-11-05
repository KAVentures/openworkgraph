"""Opt-in live Microsoft Graph organization tenant-ID verification, read-only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib import error, request

from .m365_outlook_delta import _delta_private_parent, _grant, _scope
from .m365_outlook_selected import _TOKEN, _opener, _pairs
from .m365_outlook_verified_append_receipt import _exclusive_private

FORMAT = "openworkgraph.m365-tenant-preflight.v1"
URL = "https://graph.microsoft.com/v1.0/organization?%24select=id"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\Z")
MAX_BYTES = 8192


class TenantError(ValueError):
    """Refusal without provider or credential disclosure."""


def _parse(raw: bytes, expected_tenant_id: str):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise TenantError("Invalid tenant response size")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise TenantError("Invalid tenant JSON") from None
    if (type(data) is not dict or set(data) - {"value", "@odata.context"}
            or type(data.get("value")) is not list or len(data["value"]) != 1):
        raise TenantError("Ambiguous tenant response")
    item = data["value"][0]
    if (type(item) is not dict or set(item) != {"id"}
            or type(item["id"]) is not str or not GUID.fullmatch(item["id"])
            or ("@odata.context" in data and
                (type(data["@odata.context"]) is not str or len(data["@odata.context"]) > 1024))):
        raise TenantError("Unexpected tenant fields")
    if item["id"].lower() != expected_tenant_id.lower():
        raise TenantError("Graph organization does not match expected tenant")
    return item["id"].lower()


def preflight(*, tenant, mailbox, folder, expected_tenant_id, grant_file: Path,
              output: Path, token, consent_graph_read=False,
              consent_private_report=False, consent_tenant_lookup=False, opener=None):
    if (consent_graph_read is not True or consent_private_report is not True
            or consent_tenant_lookup is not True):
        raise TenantError("Separate live read, tenant lookup and report consents required")
    if type(expected_tenant_id) is not str or not GUID.fullmatch(expected_tenant_id):
        raise TenantError("Invalid expected tenant GUID")
    if type(token) is not str or not 1 <= len(token) <= 4096 or not _TOKEN.fullmatch(token):
        raise TenantError("Invalid Graph bearer")
    grant_file, output = Path(grant_file), Path(output)
    _delta_private_parent(output.parent)
    if (output.suffix != ".json" or not output.name.startswith("tenant-preflight-")
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", output.name)
            or output == grant_file or output.exists() or output.is_symlink()):
        raise TenantError("Unsafe private report path")
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    transport = _opener() if opener is None else opener
    req = request.Request(URL, method="GET", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with transport.open(req, timeout=15) as response:
            media = response.headers.get("Content-Type", "")
            if (response.status != 200 or response.geturl() != URL
                    or type(media) is not str or
                    not re.fullmatch(r"application/json(?:\\s*;[^;\\r\\n]+)*", media, re.I)):
                raise TenantError("Unexpected Graph tenant transport")
            raw = response.read(MAX_BYTES + 1)
    except (OSError, error.URLError, ValueError) as exc:
        if isinstance(exc, TenantError):
            raise
        raise TenantError("Graph tenant read unavailable") from None
    verified = _parse(raw, expected_tenant_id)
    _grant(grant_file, tenant, mailbox, folder)
    report = {
        "format": FORMAT, "status": "tenant_identity_verified",
        "scope_sha256": scope,
        "provider_tenant_id_sha256": hashlib.sha256(verified.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "tenant_id_verified": True, "provider_read_only": True,
        "owg_import_performed": False, "checkpoint_advanced": False,
        "safe_to_retry": False,
    }
    _grant(grant_file, tenant, mailbox, folder)
    try:
        _exclusive_private(output, (json.dumps(report, sort_keys=True) + "\n").encode())
    except FileExistsError:
        raise TenantError("Report already exists") from None
    return {"format": FORMAT, "status": "tenant_identity_verified", "tenant_id_verified": True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "expected-tenant-id"):
        p.add_argument("--" + key, required=True)
    for key in ("grant-file", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    for key in ("consent-graph-read", "consent-private-report", "consent-tenant-lookup"):
        p.add_argument("--" + key, action="store_true")
    a = p.parse_args(argv)
    try:
        result = preflight(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            expected_tenant_id=a.expected_tenant_id,
            grant_file=a.grant_file, output=a.output,
            token=os.environ.get("OWG_M365_GRAPH_TOKEN"),
            consent_graph_read=a.consent_graph_read,
            consent_private_report=a.consent_private_report,
            consent_tenant_lookup=a.consent_tenant_lookup)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (TenantError, ValueError, TypeError, OSError):
        print(json.dumps({"format": FORMAT, "status": "refused"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
