from __future__ import annotations

"""Read-only portfolio triage for separately admitted, retained outcome evidence.

Only the authenticated REST caller can select an account scope. Each case is
independently admitted against its complete reviewed dependency graph, and
both private HMAC ledgers are validated before these projections are built.
A historical provider payment is never customer completion or a fresh receipt.
"""
import hashlib
import hmac
import json
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

_FORMAT = "openworkgraph.local-reviewed-outcome-portfolio.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_MAX_CASES = 50


def portfolio_fingerprint(snapshot: dict[str, Any], *, scope: str, secret: str) -> str:
    """Keyed, revision-and-incarnation-bound pagination token; no native IDs."""
    if not isinstance(snapshot, dict) or snapshot.get("format") != "openworkgraph.local-case-declarations.v1":
        raise CaseDeclarationError("invalid reviewed outcome portfolio")
    rows = snapshot.get("items")
    if not isinstance(rows, list) or len(rows) > _MAX_CASES:
        raise CaseDeclarationError("invalid bounded reviewed portfolio")
    if not isinstance(scope, str) or not scope.startswith("owg:scope:") or not isinstance(secret, str):
        raise CaseDeclarationError("invalid reviewed portfolio scope")
    bindings = []
    for row in rows:
        if not isinstance(row, dict):
            raise CaseDeclarationError("invalid reviewed portfolio case")
        bindings.append((
            row["work_item_ref"], row["revision"], row["edit_token"],
            row["goal_code"], row["expected_outcome_code"],
            row["updated_at"], row["expires_at"],
            tuple(row["depends_on_work_item_refs"]),
        ))
    bindings.sort(key=lambda item: item[0])
    if len({item[0] for item in bindings}) != len(bindings):
        raise CaseDeclarationError("duplicate reviewed portfolio case")
    payload = json.dumps(bindings, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hmac.new(secret.encode("utf-8"),
                    b"open-work:outcome-portfolio:v1:" + scope.encode("ascii") + b":" + payload,
                    hashlib.sha256).hexdigest()


def validate_page_token(expected: str | None, actual: str, *, offset: int) -> None:
    if expected is None:
        if offset != 0:
            raise CaseDeclarationError("stale portfolio pagination requires exact token")
        return
    if not isinstance(expected, str) or not _HEX.fullmatch(expected):
        raise CaseDeclarationError("invalid reviewed portfolio page token")
    if not hmac.compare_digest(expected, actual):
        raise CaseDeclarationError("stale reviewed portfolio changed")


def compose_outcome_portfolio(
    entries: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    *, total: int, offset: int, fingerprint: str,
) -> dict[str, Any]:
    """Only minimize independently verified ledger responses, never raw rows."""
    if (type(total) is not int or not 0 <= total <= _MAX_CASES or
            type(offset) is not int or not 0 <= offset <= total or
            not isinstance(entries, list) or len(entries) > 10 or
            offset + len(entries) > total or not _HEX.fullmatch(fingerprint)):
        raise CaseDeclarationError("invalid outcome portfolio page")
    cases = []
    attention_count = 0
    for row, provider, human in entries:
        ref = row["work_item_ref"]
        if (provider.get("format") != "openworkgraph.local-case-operation-evidence-ledger.v1" or
                human.get("format") != "openworkgraph.local-case-human-outcome-assessments.v1" or
                provider.get("work_item_ref") != ref or human.get("work_item_ref") != ref):
            raise CaseDeclarationError("conflicting outcome portfolio history identity")
        h_status = human["latest_human_assessed_status"]
        p_state = provider["last_observed_provider_state"]
        h_count = human["event_count"]
        p_count = provider["provider_check_count"]
        disputed = provider["disputed_check_count"]
        if (h_status not in ("met", "not_met", "unknown") or
                p_state not in ("paid", "unknown") or
                any(type(v) is not int or not 0 <= v <= 32
                    for v in (h_count, p_count, disputed))):
            raise CaseDeclarationError("invalid outcome portfolio history state")
        expected = row["expected_outcome_code"]
        reasons = []
        if h_count == 0:
            reasons.append("human_assessment_missing")
        elif h_status == "not_met":
            reasons.append("human_assessed_not_met")
        elif h_status == "unknown":
            reasons.append("human_assessed_unknown")
        # Only operation-receipt goals require a historical operation check.
        # A human-review or source-recheck goal does NOT require payment.
        if expected == "operation_receipt_reviewed":
            if p_count == 0:
                reasons.append("historical_operation_receipt_missing")
            elif p_state == "unknown":
                reasons.append("historical_operation_receipt_unknown_or_disputed")
        attention_count += bool(reasons)
        cases.append({
            "work_item_ref": ref,
            "expected_revision": row["revision"],
            "expected_edit_token": row["edit_token"],
            "owner_declared_goal_code": row["goal_code"],
            "owner_declared_expected_outcome_code": expected,
            "declared_prerequisite_count": len(row["depends_on_work_item_refs"]),
            "latest_human_assessed_status": h_status,
            "human_assessment_event_count": h_count,
            "historical_last_provider_state": p_state,
            "provider_check_count": p_count,
            "disputed_provider_check_count": disputed,
            "review_attention": reasons,
            "review_attention_present": bool(reasons),
            "historical_only_requires_fresh_source_recheck": True,
            "human_assessment_is_not_independent_verification": True,
            "expected_outcome_verified": False,
            "customer_outcome_verified": False,
        })
    next_offset = offset + len(cases)
    return {
        "format": _FORMAT,
        "portfolio_digest": fingerprint,
        "total_reviewed_cases": total,
        "offset": offset,
        "included_case_count": len(cases),
        "next_offset": next_offset if next_offset < total else None,
        "remaining_case_count": total - next_offset,
        "cases_with_review_attention": attention_count,
        "cases": cases,
        "historical_only_requires_fresh_source_recheck": True,
        "separate_local_ledger_reads_not_atomic_snapshot": True,
        "human_assessments_are_not_independent_verification": True,
        "current_provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_read_performed": False,
        "source_write_performed": False,
        "not_synced": True,
    }


__all__ = ["portfolio_fingerprint", "validate_page_token", "compose_outcome_portfolio"]
