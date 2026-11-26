"""Independent historical audit timeline with explicit per-snapshot trust.

No live authorization, trusted chronology, key-rotation attestation or
claim that the provided snapshots cover all historical incidents.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field

from shared.agent_procurement import Strict, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_dossier as audit

MAX_SNAPSHOTS = 12
MAX_INPUT_BYTES = 160 * 1024 * 1024


class Entry(Strict):
    dossier_sha256: Hash
    trust: audit.TrustPins


class Manifest(Strict):
    format: Literal["openworkgraph.workforce-audit-timeline-manifest.v1"]
    entries: list[Entry] = Field(min_length=2, max_length=MAX_SNAPSHOTS)


def _key_fingerprint(key):
    return sha256(bytes.fromhex(key)).hexdigest()


def _site_history(previous, current):
    """Check all previously seen aliases, including temporarily absent ones."""
    rows = []
    for alias in sorted(set(previous) | set(current)):
        a,b = previous.get(alias),current.get(alias)
        if b is None:
            continue
        if a is not None:
            if b["timestamp"] < a["timestamp"] or (
                b["timestamp"] == a["timestamp"] and b["pin"] != a["pin"]
            ):
                raise ValueError("older or conflicting packet for historical installation alias")
            if b["key"] != a["key"] and b["key"] in a["retired_keys"]:
                raise ValueError("previously retired signing key reappeared for alias")
        keys = set(a["retired_keys"]) if a else set()
        changed = a is not None and a["key"] != b["key"]
        if changed:
            keys.add(a["key"])
        rows.append((alias, {**b, "retired_keys": keys}, changed))
    return rows


def review(documents, manifest, *, expected_manifest_sha256):
    """Recompute each supplied report, with separately pinned historical keys.

    Trust manifest MUST be obtained through an independent administrative
    channel. Its pin binds ordering, archive digests and historical key sets.
    """
    parsed = Manifest.model_validate(manifest)
    if audit._canonical(parsed.model_dump()) != audit._canonical(manifest):
        raise ValueError("noncanonical historical trust manifest")
    audit._pin(manifest, expected_manifest_sha256)
    if len(documents) != len(parsed.entries):
        raise ValueError("exact ordered dossier set required")
    if len({e.dossier_sha256 for e in parsed.entries}) != len(parsed.entries):
        raise ValueError("duplicate historical dossier")
    seen_sites, seen_rows, key_owners, casefold_aliases = {}, {}, {}, {}
    previous_sites, previous_rows = set(), set()
    snapshots = []
    previous_clock = None
    namespace = None
    for i,(doc,entry) in enumerate(zip(documents,parsed.entries)):
        receipt = audit.verify(doc,entry.trust.model_dump(),
                               expected_dossier_sha256=entry.dossier_sha256)
        clock = receipt["reviewed_as_of_ms"]
        ns = doc["correlation_namespace_sha256"]
        if namespace is None:
            namespace = ns
        if ns != namespace or (previous_clock is not None and clock <= previous_clock):
            raise ValueError("one namespace and strictly advancing review clocks required")
        keys = {t.installation_alias: _key_fingerprint(t.public_key_hex)
                for t in entry.trust.trust}
        current = {}
        for p in doc["packets"]:
            packet = p["packet"]
            alias = packet["installation_alias"]
            fold = alias.casefold()
            key = keys[alias]
            if casefold_aliases.setdefault(fold,alias) != alias:
                raise ValueError("historical installation alias spelling changed")
            if key_owners.setdefault(key,alias) != alias:
                raise ValueError("historical signing key reassigned to another alias")
            current[alias] = {"timestamp": packet["exported_at_ms"],
                              "pin": p["expected_packet_sha256"], "key": key}
        rotations = []
        for alias, item, changed in _site_history(seen_sites,current):
            if changed:
                rotations.append({"installation_alias": alias,
                                  "previous_key_sha256": seen_sites[alias]["key"],
                                  "current_key_sha256": item["key"],
                                  "rotation_authority_verified": False})
            seen_sites[alias] = item
        scopes = audit._row_index(doc)
        report_rows = {(r["installation_alias"],r["journal_tag"]):r
                       for r in doc["report"]["journals"]}
        for scope,row in sorted(scopes.items()):
            old = seen_rows.get(scope)
            previous_high = old["maximum_cutoff_ms"] if old else None
            # Retain anomalous regressions for review, never erase older facts.
            item = old if old is not None else {
                "installation_alias": scope[0], "journal_tag": scope[1],
                "first_snapshot": i+1, "observed_snapshots": 0,
                "rollback_referral_snapshots": [], "cutoff_regression_snapshots": [],
                "excluded_snapshots": [], "changed_cohort_snapshots": [],
                "latest_cutoff_ms": None, "maximum_cutoff_ms": None,
                "_cohort": None}
            cohort = (row["plan_tag"],frozenset(row["case_tags"])) if row["cohort_identity_available"] else None
            if old is not None and (cohort is None or item["_cohort"] is None or item["_cohort"] != cohort):
                item["changed_cohort_snapshots"].append(i+1)
            if previous_high is not None and row["cutoff_ms"] < previous_high:
                item["cutoff_regression_snapshots"].append(i+1)
            if row["rollback_review"]:
                item["rollback_referral_snapshots"].append(i+1)
            if not report_rows[scope]["included_in_totals"]:
                item["excluded_snapshots"].append(i+1)
            item.update({"last_snapshot": i+1,"observed_snapshots": item["observed_snapshots"]+1,
                         "latest_cutoff_ms": row["cutoff_ms"],
                         "maximum_cutoff_ms": max(row["cutoff_ms"],previous_high if previous_high is not None else 0),
                         "_cohort": cohort})
            seen_rows[scope] = item
        sites, rows = set(current),set(scopes)
        snapshots.append({"snapshot": i+1,"dossier_sha256": entry.dossier_sha256,
            "report_sha256": receipt["report_sha256"],"reviewed_as_of_ms": clock,
            "maximum_packet_age_ms": doc["maximum_packet_age_ms"],
            "packet_age_policy_changed": i>0 and doc["maximum_packet_age_ms"]!=snapshots[-1]["maximum_packet_age_ms"],
            "active_supplied_aliases": sorted(sites),
            "aliases_added_since_previous": sorted(sites-previous_sites),
            "aliases_absent_since_previous": sorted(previous_sites-sites),
            "declared_key_changes": rotations,
            "journals_present": len(rows),"journals_absent_since_previous": len(previous_rows-rows),
            "included_journals": doc["report"]["included_journals"],
            "excluded_journals": doc["report"]["excluded_journals"],
            "unreadable_journals": doc["report"]["unreadable_journals"]})
        previous_sites,previous_rows,previous_clock = sites,rows,clock
    out=[]
    for scope,item in sorted(seen_rows.items()):
        row={k:v for k,v in item.items() if k!="_cohort"}
        row["present_in_latest_snapshot"]=scope in previous_rows
        row["historical_rollback_referral_observed"]=bool(row["rollback_referral_snapshots"])
        out.append(row)
    body={"format":"openworkgraph.workforce-audit-timeline.v1",
          "manifest_sha256":expected_manifest_sha256,"correlation_namespace_sha256":namespace,
          "snapshots":snapshots,"journals":out,
          "unique_journals_with_observed_rollback_referrals":sum(r["historical_rollback_referral_observed"] for r in out),
          "historical_reports_recomputed":True,"key_rotation_authority_verified":False,
          "complete_incident_history_verified":False,"trusted_timestamp_verified":False,
          "incident_clearance_established":False,"financial_delta_calculated":False,
          "execution_authorized":False,
          "limitations":["Each supplied snapshot is independently verified against its external pins.",
                         "Changed key sets and absent installations do not prove authorization or revocation.",
                         "No snapshots are added; absent journals and missing intervals are not evidence of clearance.",
                         "Exact opaque tags do not establish true business identity."]}
    return {**body,"timeline_sha256":digest(body)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",type=Path)
    parser.add_argument("--manifest-sha256",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("dossiers",nargs="+",type=Path)
    args=parser.parse_args(argv)
    try:
        if not 2<=len(args.dossiers)<=MAX_SNAPSHOTS:
            raise ValueError("invalid timeline size")
        # Check actual canonical sizes as well as file sizes before retaining.
        documents=[];total=0
        for path in args.dossiers:
            total+=path.stat().st_size
            if total>MAX_INPUT_BYTES:
                raise ValueError("aggregate timeline input too large")
            doc=audit.read_json(path)
            documents.append(doc)
        if sum(len(audit._canonical(d)) for d in documents)>MAX_INPUT_BYTES:
            raise ValueError("aggregate canonical input too large")
        result=review(documents,audit.read_json(args.manifest),
                      expected_manifest_sha256=args.manifest_sha256)
        audit.write_json(args.output,result)
    except (ValueError,TypeError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Timeline refused: invalid historical evidence, policy, or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
