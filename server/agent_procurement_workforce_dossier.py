"""Portable, independently recomputable signed workforce audit dossiers.

Historical declared evidence, not live authorization or an attestation of
reviewer identity, source truth, completeness, or trusted capture time.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from shared.agent_procurement import Strict, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_signing as signing
from . import agent_procurement_workforce_exchange as exchange

FORMAT = "openworkgraph.workforce-audit-dossier.v1"
MAX_BYTES = 40 * 1024 * 1024
Age = Annotated[StrictInt, Field(ge=1, le=366*24*3600000)]


class Dossier(Strict):
    format: Literal["openworkgraph.workforce-audit-dossier.v1"]
    correlation_namespace_sha256: Hash
    reviewed_as_of_ms: exchange.Epoch
    maximum_packet_age_ms: Age
    packets: list[signing.SignedPacket] = Field(min_length=1, max_length=exchange.MAX_SITES)
    report: dict
    dossier_sha256: Hash


class TrustPins(Strict):
    expected_correlation_namespace_sha256: Hash
    trust: list[signing.Trust] = Field(min_length=1, max_length=exchange.MAX_SITES)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _pin(value, expected):
    if digest(value) != expected:
        raise ValueError("externally supplied dossier digest mismatch")


def _request(namespace, age, packets, trust):
    return {"expected_correlation_namespace_sha256": namespace,
            "maximum_packet_age_ms": age, "packets": packets, "trust": trust,
            "confirmed": True}


def freeze(document, *, now=None):
    """Produce a dossier only from complete, explicitly pinned signed input."""
    req = signing.SignedReview.model_validate(document)
    if not req.confirmed:
        raise ValueError("explicit dossier creation confirmation required")
    # Validate the time through the dossier schema below; bool is not an epoch.
    now = int(time.time()*1000) if now is None else now
    if type(now) is not int or not 0 <= now <= 10**15:
        raise ValueError("invalid review clock")
    packets = sorted((p.model_dump() for p in req.packets),
                     key=lambda p: p["packet"]["installation_alias"])
    trust = [t.model_dump() for t in req.trust]
    namespace = req.expected_correlation_namespace_sha256
    report = signing.signed_review(_request(namespace, req.maximum_packet_age_ms,
                                            packets, trust), now=now)
    body = {"format": FORMAT, "correlation_namespace_sha256": namespace,
            "reviewed_as_of_ms": now, "maximum_packet_age_ms": req.maximum_packet_age_ms,
            "packets": packets, "report": report}
    result = {**body, "dossier_sha256": digest(body)}
    Dossier.model_validate(result)
    if len(_canonical(result)) > MAX_BYTES:
        raise ValueError("dossier exceeds portable size limit")
    return result


def verify(document, pins, *, expected_dossier_sha256):
    """Recompute all metrics and signatures using separately supplied keys.

    Embedded fingerprints are never used as trust anchors. The caller must
    separately obtain both the archive digest and the trust key mapping.
    """
    parsed = Dossier.model_validate(document)
    trusted = TrustPins.model_validate(pins)
    if parsed.correlation_namespace_sha256 != trusted.expected_correlation_namespace_sha256:
        raise ValueError("external trust namespace mismatch")
    body = dict(document)
    internal = body.pop("dossier_sha256")
    _pin(body, expected_dossier_sha256)
    if internal != expected_dossier_sha256:
        raise ValueError("embedded dossier digest mismatch")
    # Require the canonical wire values, not Pydantic-coerced approximations.
    packets = [p.model_dump() for p in parsed.packets]
    if _canonical(packets) != _canonical(document["packets"]):
        raise ValueError("noncanonical packet representation")
    if packets != sorted(packets, key=lambda p: p["packet"]["installation_alias"]):
        raise ValueError("noncanonical installation ordering")
    report = signing.signed_review(_request(parsed.correlation_namespace_sha256,
        parsed.maximum_packet_age_ms, packets, [t.model_dump() for t in trusted.trust]),
        now=parsed.reviewed_as_of_ms)
    if _canonical(report) != _canonical(parsed.report):
        raise ValueError("archived report does not match independently recomputed evidence")
    return {"format": "openworkgraph.workforce-audit-verification.v1",
            "dossier_sha256": internal, "report_sha256": report["report_sha256"],
            "reviewed_as_of_ms": parsed.reviewed_as_of_ms,
            "installation_count": len(packets), "historical_signatures_recomputed": True,
            "historical_report_recomputed": True, "current_freshness_verified": False,
            "trusted_timestamp_verified": False, "source_truth_verified": False,
            "execution_authorized": False}


def _row_index(dossier):
    return {(p["packet"]["installation_alias"], r["journal_tag"]): r
            for p in dossier["packets"] for r in p["packet"]["rows"]}


def _change_rows(previous, current):
    """A membership/exclusion diff, never sum the two snapshots."""
    a, b = _row_index(previous), _row_index(current)
    ra = {(r["installation_alias"], r["journal_tag"]): r
          for r in previous["report"]["journals"]}
    rb = {(r["installation_alias"], r["journal_tag"]): r
          for r in current["report"]["journals"]}
    out = []
    for scope in sorted(set(a) | set(b)):
        old, new = a.get(scope), b.get(scope)
        prior, latest = ra.get(scope), rb.get(scope)
        if old is None:
            status = "newly_present"
        elif new is None:
            status = "no_longer_present"
        elif (old["plan_tag"] != new["plan_tag"]
              or not old["cohort_identity_available"] or not new["cohort_identity_available"]
              or set(old["case_tags"]) != set(new["case_tags"])):
            status = "changed_or_unavailable_cohort"
        else:
            status = "same_declared_case_cohort"
        if old is not None and new is not None and new["cutoff_ms"] < old["cutoff_ms"]:
            status = "journal_cutoff_regressed"
        out.append({"installation_alias": scope[0], "journal_tag": scope[1],
            "membership": status,
            "previous_included": prior["included_in_totals"] if prior else None,
            "current_included": latest["included_in_totals"] if latest else None,
            "previous_exclusion_reasons": prior["exclusion_reasons"] if prior else None,
            "current_exclusion_reasons": latest["exclusion_reasons"] if latest else None,
            "previous_recorded_cases": old["recorded_cases"] if old else None,
            "current_recorded_cases": new["recorded_cases"] if new else None,
            "prior_rollback_referral": bool(old and old["rollback_review"]),
            "current_rollback_referral": bool(new and new["rollback_review"]),
            "rollback_referral_observed_in_either_snapshot":
                bool((old and old["rollback_review"]) or (new and new["rollback_review"]))})
    return out


def compare(previous, current, pins, *, previous_sha256, current_sha256):
    """Review membership and rollback signals across two verified snapshots.

    Deliberately refuse changed namespace/trust/policy and nonmonotone packet
    timestamps. This is a two-snapshot comparison, not full incident history.
    """
    verify(previous, pins, expected_dossier_sha256=previous_sha256)
    verify(current, pins, expected_dossier_sha256=current_sha256)
    if current["reviewed_as_of_ms"] <= previous["reviewed_as_of_ms"]:
        raise ValueError("review clock must advance")
    if current["maximum_packet_age_ms"] != previous["maximum_packet_age_ms"]:
        raise ValueError("compare dossiers under the same packet-age policy")
    old = {p["packet"]["installation_alias"]: p for p in previous["packets"]}
    new = {p["packet"]["installation_alias"]: p for p in current["packets"]}
    if set(old) != set(new):
        raise ValueError("exact same trusted installations required")
    sites = []
    for alias in sorted(old):
        a,b = old[alias],new[alias]
        before,after = a["packet"]["exported_at_ms"],b["packet"]["exported_at_ms"]
        same = a["expected_packet_sha256"] == b["expected_packet_sha256"]
        if after < before or (after == before and not same):
            raise ValueError("older or conflicting installation packet")
        sites.append({"installation_alias": alias, "packet_advanced": after > before,
                      "previous_packet_sha256": a["expected_packet_sha256"],
                      "current_packet_sha256": b["expected_packet_sha256"]})
    rows = _change_rows(previous, current)
    body = {"format": "openworkgraph.workforce-audit-delta.v1",
            "previous_dossier_sha256": previous_sha256, "current_dossier_sha256": current_sha256,
            "installations": sites, "journals": rows,
            "prior_or_current_rollback_referrals": sum(r["rollback_referral_observed_in_either_snapshot"] for r in rows),
            "incident_clearance_established": False, "complete_incident_history_verified": False,
            "financial_delta_calculated": False, "execution_authorized": False,
            "limitations": ["No snapshots are added together; the same cases may recur.",
                            "Absence of a prior incident in a later packet is not clearance.",
                            "Exact tags and signatures do not establish business identity or source truth."]}
    return {**body, "comparison_sha256": digest(body)}


def _unique_pairs(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("non-finite JSON constant")


def read_json(path):
    with open(path, "rb") as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError("input exceeds dossier size limit")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                       parse_constant=_bad_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def write_json(path, value):
    data = _canonical(value) + b"\n"
    if len(data) > MAX_BYTES:
        raise ValueError("output exceeds dossier size limit")
    # Never overwrite existing files (including symlinks); private on POSIX.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("freeze")
    create.add_argument("request", type=Path)
    create.add_argument("output", type=Path)
    create.add_argument("--confirm-linkable-export", action="store_true", required=True)
    check = sub.add_parser("verify")
    check.add_argument("dossier", type=Path)
    check.add_argument("trust", type=Path)
    check.add_argument("--sha256", required=True)
    check.add_argument("output", type=Path)
    delta = sub.add_parser("compare")
    delta.add_argument("previous", type=Path)
    delta.add_argument("current", type=Path)
    delta.add_argument("trust", type=Path)
    delta.add_argument("--previous-sha256", required=True)
    delta.add_argument("--current-sha256", required=True)
    delta.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "freeze":
            result = freeze(read_json(args.request))
        elif args.action == "verify":
            result = verify(read_json(args.dossier), read_json(args.trust),
                            expected_dossier_sha256=args.sha256)
        else:
            result = compare(read_json(args.previous), read_json(args.current),
                             read_json(args.trust), previous_sha256=args.previous_sha256,
                             current_sha256=args.current_sha256)
        write_json(args.output, result)
    except (ValueError, TypeError, OSError, RecursionError):
        # Do not log potentially linkable payloads or filesystem details.
        parser.exit(2, "Dossier operation refused: invalid evidence, policy, or file access.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
