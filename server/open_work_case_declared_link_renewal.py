from __future__ import annotations

"""Renew a historically source-joined payment link without re-scanning Stripe.

A 24-hour operator-carried record link may expire after its invoice leaves the
open-invoice inventory. Only a current, same-record HMAC-validated provider
check history can anchor a new link. Renewal never verifies current payment,
reads a provider, writes evidence, or authorizes an agent.
"""
from datetime import timedelta
import hmac
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_declared_outcome import _ledger, require_declared_payment_goal
from .open_work_case_operation_verification import (
    _FIELDS as _LINK_FIELDS, _FORMAT as _LINK_FORMAT,
    _MAX_AGE as _LINK_MAX_AGE, _record_ref, validate_case_record_link,
)
from .open_work_case_status_checkpoint import (
    _bindings, _canonical, _clock, _mac, _parse_utc,
)

_FORMAT = "openworkgraph.local-case-declared-record-link-renewal.v1"
_MAX_HISTORICAL_LINK_AGE = timedelta(days=7)
_CLOCK_SKEW = timedelta(minutes=5)


def validate_historical_payment_record_link(
    link: Any, admitted: dict[str, Any], signature: tuple, *,
    scope: str, secret: str, now=None,
) -> str:
    """Authenticate an old link without accepting it as a current source scan.

    Only the expiry/age check differs from the normal record-link validator;
    exact scope, owner revision, DAG, record identity and HMAC remain mandatory.
    The original link must have been issued within the current retention window.
    """
    ref = require_declared_payment_goal(admitted)
    if not isinstance(link, dict) or set(link) != _LINK_FIELDS:
        raise CaseDeclarationError("invalid historical payment record link")
    if (link["format"] != _LINK_FORMAT or link["work_item_ref"] != ref or
            type(link["revision"]) is not int or
            link["revision"] != admitted["owner_declaration"]["revision"] or
            link["expected_operation"] != "stripe_invoice_paid"):
        raise CaseDeclarationError("stale historical payment record link")
    resource_ref = _record_ref(link["resource_ref"])
    graph, source, key = _bindings(signature, scope, secret)
    for field, expected in (("graph_binding", graph), ("source_binding", source)):
        value = link[field]
        if (not isinstance(value, str) or len(value) != 64 or
                not hmac.compare_digest(value, expected)):
            raise CaseDeclarationError("stale historical payment source scope")
    current = _clock(now)
    issued, expiry = _parse_utc(link["issued_at"]), _parse_utc(link["expires_at"])
    owner_expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    if (issued > current + _CLOCK_SKEW or expiry <= issued or
            expiry - issued > _LINK_MAX_AGE or expiry > owner_expiry or
            current >= owner_expiry or
            current - issued > _MAX_HISTORICAL_LINK_AGE):
        raise CaseDeclarationError("historical payment link outside reviewed retention")
    seal = link["seal"]
    if not isinstance(seal, str) or len(seal) != 64:
        raise CaseDeclarationError("invalid historical payment link seal")
    unsigned = {field: link[field] for field in _LINK_FIELDS if field != "seal"}
    if not hmac.compare_digest(
        seal, _mac(key, b"case-record-link", _canonical(unsigned))
    ):
        raise CaseDeclarationError("tampered historical payment link")
    return resource_ref


def renew_declared_payment_record_link(
    admitted: dict[str, Any], signature: tuple, old_link: Any,
    history: dict[str, Any], *, scope: str, secret: str, now=None,
) -> dict[str, Any]:
    """Return a new bounded operator-carried link, not a provider receipt.

    The caller must obtain history using read_operation_evidence with the
    validated exact resource_ref and the live owner/source guard. That ledger
    verifies its HMAC chain and original record binding before this function.
    """
    current = _clock(now)
    resource_ref = validate_historical_payment_record_link(
        old_link, admitted, signature, scope=scope, secret=secret, now=current,
    )
    count, disputes, state = _ledger(admitted, history)
    events = history.get("events")
    if (count < 1 or disputes != 0 or
            not isinstance(events, list) or
            history.get("event_count") != len(events) or
            not 1 <= len(events) <= 32 or
            sum(isinstance(event, dict) and event.get("kind") == "check"
                for event in events) != count or
            any(not isinstance(event, dict) or event.get("kind") != "check" or
                event.get("disputed") is not False for event in events)):
        raise CaseDeclarationError("undisputed retained source check required for link renewal")
    graph, source, key = _bindings(signature, scope, secret)
    owner_expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    expiry = min(current + _LINK_MAX_AGE, owner_expiry)
    if expiry <= current:
        raise CaseDeclarationError("reviewed payment link retention expired")
    unsigned = {
        "format": _LINK_FORMAT,
        "work_item_ref": admitted["work_item_ref"],
        "resource_ref": resource_ref,
        "revision": admitted["owner_declaration"]["revision"],
        "expected_operation": "stripe_invoice_paid",
        "graph_binding": graph,
        "source_binding": source,
        "issued_at": current.isoformat(),
        "expires_at": expiry.isoformat(),
    }
    link = {
        **unsigned,
        "seal": _mac(key, b"case-record-link", _canonical(unsigned)),
    }
    # Ensure the renewed link satisfies the original fresh-verification API's
    # unmodified validator, rather than introducing a second token dialect.
    validate_case_record_link(
        link, admitted, signature, scope=scope, secret=secret, now=current,
    )
    return {
        "format": _FORMAT,
        "work_item_ref": admitted["work_item_ref"],
        "record_link": link,
        "prior_link_expired": _parse_utc(old_link["expires_at"]) < current,
        "historical_provider_check_count": count,
        "historical_last_provider_state": state,
        "historical_payment_not_current": True,
        "historical_source_join_not_current_open_inventory": True,
        "fresh_source_recheck_required": True,
        "provider_goal_met_now": False,
        "customer_outcome_verified": False,
        "business_objective_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_read_performed": False,
        "source_write_performed": False,
        "new_evidence_appended": False,
        "server_side_link_persistence": False,
        "not_synced": True,
    }


__all__ = [
    "validate_historical_payment_record_link",
    "renew_declared_payment_record_link",
]
