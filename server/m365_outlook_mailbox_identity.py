"""Consent-gated live Microsoft Graph mailbox identity preflight (read only).

The provider's /me identity is checked against an explicitly granted mailbox.
This does not establish the token's tenant or authorize any sync/checkpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib import error, request

from .m365_outlook_delta import _scope, _grant, _delta_private_parent
from .m365_outlook_selected import _TOKEN, _opener, _pairs
from .m365_outlook_verified_append_receipt import _exclusive_private

FORMAT = "openworkgraph.m365-mailbox-identity.v1"
URL = "https://graph.microsoft.com/v1.0/me?%24select=id,userPrincipalName,mail"
MAX_BYTES = 8192
ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_\x60{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253}[A-Za-z0-9])?\\Z", re.ASCII)


class IdentityError(ValueError):
    """Non-sensitive identity verification refusal."""


def _parse_identity(raw: bytes, expected_address: str):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise IdentityError("Invalid Graph identity response size")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise IdentityError("Invalid Graph identity response") from None
    if type(value) is not dict or set(value) - {"id", "userPrincipalName", "mail", "@odata.context"}:
        raise IdentityError("Unexpected Graph identity schema")
    if (type(value.get("id")) is not str or not 1 <= len(value["id"]) <= 256
            or type(value.get("userPrincipalName")) is not str
            or not 1 <= len(value["userPrincipalName"]) <= 320
            or (value.get("mail") is not None
                and (type(value["mail"]) is not str or not 1 <= len(value["mail"]) <= 320))
            or ("@odata.context" in value
                and (type(value["@odata.context"]) is not str or len(value["@odata.context"]) > 1024))):
        raise IdentityError("Invalid Graph identity fields")
    # Exact identity matching only. Do not treat aliases or display names as authority.
    mailbox = expected_address.casefold()
    matched_upn = value["userPrincipalName"].casefold() == mailbox
    matched_mail = value.get("mail") is not None and value["mail"].casefold() == mailbox
    if not matched_upn and not matched_mail:
        raise IdentityError("Graph account does not match granted mailbox")
    return value["id"], matched_upn, matched_mail


def preflight(*, tenant, mailbox, folder, expected_address, grant_file: Path, output: Path,
              token, consent_graph_read=False, consent_private_report=False,
              opener=None):
    if consent_graph_read is not True or consent_private_report is not True:
        raise IdentityError("Separate Graph read and private report consent required")
    scope = _scope(tenant, mailbox, folder)
    if type(expected_address) is not str or len(expected_address) > 320 or not ADDRESS.fullmatch(expected_address):
        raise IdentityError("Invalid expected mailbox address")
    if type(token) is not str or not 1 <= len(token) <= 4096 or not _TOKEN.fullmatch(token):
        raise IdentityError("Invalid Graph bearer token")
    grant_file, output = Path(grant_file), Path(output)
    _delta_private_parent(output.parent)
    if (output.suffix != ".json" or not output.name.startswith("mailbox-identity-")
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for c in output.name)
            or output == grant_file or output.exists() or output.is_symlink()):
        raise IdentityError("Unsafe private report destination")
    _grant(grant_file, tenant, mailbox, folder)
    req = request.Request(URL, method="GET", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json"})
    transport = _opener() if opener is None else opener
    try:
        with transport.open(req, timeout=15) as response:
            if (response.status != 200 or response.geturl() != URL
                    or not response.headers.get("Content-Type", "").lower().startswith("application/json")):
                raise IdentityError("Unexpected Graph response")
            raw = response.read(MAX_BYTES + 1)
    except (OSError, error.URLError, ValueError) as exc:
        if isinstance(exc, IdentityError):
            raise
        raise IdentityError("Graph identity read unavailable") from None
    identity, upn, mail = _parse_identity(raw, expected_address)
    _grant(grant_file, tenant, mailbox, folder)
    report = {
        "format": FORMAT, "status": "mailbox_identity_observed",
        "scope_sha256": scope,
        "expected_address_sha256": hashlib.sha256(expected_address.casefold().encode()).hexdigest(),
        "provider_user_id_sha256": hashlib.sha256(identity.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "upn_matched": upn, "mail_matched": mail,
        "mailbox_identifier_matched": True, "tenant_id_verified": False,
        "provider_read_only": True, "owg_import_performed": False,
        "checkpoint_advanced": False, "safe_to_retry": False,
    }
    _grant(grant_file, tenant, mailbox, folder)
    try:
        _exclusive_private(output, (json.dumps(report, sort_keys=True) + "\n").encode())
    except FileExistsError:
        raise IdentityError("Report already exists") from None
    return {"format": FORMAT, "status": report["status"],
            "mailbox_identifier_matched": True, "tenant_id_verified": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "expected-address"):
        p.add_argument("--" + key, required=True)
    for key in ("grant-file", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--consent-graph-read", action="store_true")
    p.add_argument("--consent-private-report", action="store_true")
    a = p.parse_args(argv)
    try:
        print(json.dumps(preflight(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            expected_address=a.expected_address,
            grant_file=a.grant_file, output=a.output,
            token=os.environ.get("OWG_M365_GRAPH_TOKEN"),
            consent_graph_read=a.consent_graph_read,
            consent_private_report=a.consent_private_report), sort_keys=True))
        return 0
    except (IdentityError, ValueError, TypeError, OSError):
        print(json.dumps({"format": FORMAT, "status": "refused"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
