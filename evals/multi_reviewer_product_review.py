"""Two independent blinded reviews, exact disagreements, and third-reviewer adjudication.

Offline and deliberately separate from the single-review workflow. Reviewer
identities, independence and condition-neutral source material are operator
declarations, not authenticated facts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unicodedata import normalize

from .blinded_product_review import SCHEMA, _digest, _rows, _text, _write_new, reconcile
from .product_value import ARMS, REVIEW_FIELDS, _index, _validated_review

FORMAT = "owg.multi-review-adjudication.v1"
MAX_EPISODES = 500
MAX_INPUT_BYTES = 32 * 1024 * 1024
SET_FIELDS = ("selected_resources", "selected_agent_runs")
COUNT_FIELDS = ("clarification_questions", "wrong_assumptions")
BINARY_FIELDS = ("continuation_success", "consequential_wrong_action")


def _identity(value):
    return normalize("NFKC", _text(value, "reviewer ID")).strip().casefold()


def _context(episodes, packet, key):
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= MAX_EPISODES:
        raise ValueError("select 1–500 frozen episodes")
    if not isinstance(packet, dict) or not isinstance(key, dict):
        raise ValueError("invalid frozen packet or key")
    if packet.get("schema") != SCHEMA or key.get("schema") != SCHEMA:
        raise ValueError("unsupported packet/key schema")
    if key.get("packet_digest") != _digest(packet) or key.get("truth_digest") != _digest(episodes):
        raise ValueError("packet or truth differs from frozen study")
    truth = _index(episodes, label="ground truth")
    if not isinstance(packet.get("entries"), list) or not isinstance(key.get("mapping"), dict):
        raise ValueError("invalid frozen review entries or mapping")
    entries, bindings, models, states = {}, set(), {}, {}
    mapping = key["mapping"]
    for entry in packet["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"review_id", "prompt", "review_text"}:
            raise ValueError("unexpected public review entry fields")
        rid = _text(entry["review_id"], "review ID")
        if rid in entries:
            raise ValueError("duplicate public review ID")
        for field in ("prompt", "review_text"):
            _text(entry[field], field)
        meta = mapping.get(rid)
        if not isinstance(meta, dict):
            raise ValueError("missing private binding")
        arm, eid = meta.get("arm"), meta.get("episode_id")
        if arm not in ARMS or eid not in truth or (arm, eid) in bindings:
            raise ValueError("invalid or duplicate private binding")
        bindings.add((arm, eid))
        models.setdefault(eid, set()).add(_text(meta.get("model_id"), "model ID"))
        states.setdefault(eid, set()).add(_text(meta.get("source_state_id"), "source state"))
        entries[rid] = entry
    if set(entries) != set(mapping) or bindings != {(a, e) for a in ARMS for e in truth}:
        raise ValueError("incomplete paired coverage")
    if any(len(models[e]) != 1 or len(states[e]) != 1 for e in truth):
        raise ValueError("paired model/source state mismatch")
    authors = {_identity(e["ground_truth_verified_by"]) for e in truth.values()
               if e.get("ground_truth_verified_by")}
    return entries, authors


def _sheet(rows, expected, forbidden, *, adjudication=False):
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("review sheet must cover exactly its assigned entries")
    indexed, reviewers = {}, set()
    fields = {"review_id", "reviewer_id", *REVIEW_FIELDS}
    if adjudication:
        fields.add("resolution_reason")
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("review sheet contains missing or unexpected fields")
        rid = _text(row["review_id"], "review ID")
        if rid not in expected or rid in indexed:
            raise ValueError("unknown or duplicate review ID")
        who = _identity(row["reviewer_id"])
        if who in forbidden:
            raise ValueError("reviewer must differ from truth authors and other reviewers")
        reviewers.add(who)
        # Reuse the canonical validation for binary and count fields.
        _validated_review({"recorded_model_completion": True, "independent_review": row},
                          episode_id="opaque", arm="blinded")
        item = {"review_id": rid, "reviewer_id": row["reviewer_id"]}
        for field in REVIEW_FIELDS:
            value = row[field]
            if field in SET_FIELDS:
                if (not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value)
                        or len(set(value)) != len(value)):
                    raise ValueError("selected evidence must contain distinct nonempty string IDs")
                value = sorted(value)
            item[field] = value
        if adjudication:
            item["resolution_reason"] = _text(row["resolution_reason"], "resolution reason")
        indexed[rid] = item
    if set(indexed) != set(expected) or len(reviewers) != (1 if expected else 0):
        raise ValueError("each independent sheet must identify one reviewer and cover all assigned entries")
    return indexed, reviewers


def _agreement(first, second):
    n = len(first)
    fields = {}
    for field in REVIEW_FIELDS:
        pairs = [(first[rid][field], second[rid][field]) for rid in first]
        equal = sum(a == b for a, b in pairs)
        result = {"exact_agreement_count": equal, "exact_agreement_rate": equal / n}
        if field in BINARY_FIELDS:
            tt = sum(a and b for a, b in pairs)
            ff = sum(not a and not b for a, b in pairs)
            tf = sum(a and not b for a, b in pairs)
            ft = sum(not a and b for a, b in pairs)
            # Integer form avoids floating-point cancellation at degenerate marginals.
            expected = (tt + tf) * (tt + ft) + (ff + ft) * (ff + tf)
            result.update(both_true=tt, both_false=ff, first_only_true=tf, second_only_true=ft,
                          cohen_kappa=(equal * n - expected) / (n*n - expected)
                          if expected != n*n else None)
        elif field in COUNT_FIELDS:
            result["mean_absolute_difference"] = sum(abs(a-b) for a, b in pairs) / n
        else:
            # Both-empty sets agree exactly; do not omit empty selections.
            result["mean_jaccard"] = sum(len(set(a) & set(b)) / len(set(a) | set(b))
                                         if a or b else 1.0 for a, b in pairs) / n
        fields[field] = result
    return fields


def prepare_adjudication(episodes, packet, key, first_review, second_review):
    """Produce a condition-neutral disagreement packet and a private frozen basis."""
    entries, authors = _context(episodes, packet, key)
    first, first_ids = _sheet(first_review, entries, authors)
    second, _ = _sheet(second_review, entries, authors | first_ids)
    disagreements = []
    for rid, entry in entries.items():
        differing = [f for f in REVIEW_FIELDS if first[rid][f] != second[rid][f]]
        if differing:
            disagreements.append({
                **entry, "differing_fields": differing,
                "first_review": {f: first[rid][f] for f in REVIEW_FIELDS},
                "second_review": {f: second[rid][f] for f in REVIEW_FIELDS},
            })
    public = {"schema": FORMAT, "entries": disagreements,
              "limitations": "Operator-reviewed material; blinding and independence are not verified."}
    basis = {"schema": FORMAT, "original_packet_sha256": _digest(packet),
             "original_key_sha256": _digest(key), "truth_sha256": _digest(episodes),
             "first_review_sha256": _digest(first_review), "second_review_sha256": _digest(second_review),
             "adjudication_packet_sha256": _digest(public)}
    agreement = {"reviewed_entries": len(entries), "agreement_entries": len(entries)-len(disagreements),
                 "disagreement_entries": len(disagreements),
                 "fields": _agreement(first, second),
                 "reviewer_independence_independently_verified": False,
                 "blinding_independently_verified": False}
    return public, basis, agreement


def adjudication_template(packet):
    return [{"review_id": row["review_id"], "reviewer_id": None,
             **{field: None for field in REVIEW_FIELDS}, "resolution_reason": None}
            for row in packet["entries"]]


def score_adjudicated(episodes, packet, key, first_review, second_review,
                      adjudication_packet, basis, resolutions):
    """Refuse changed input or missing resolutions; score exact final judgments."""
    expected_packet, expected_basis, agreement = prepare_adjudication(
        episodes, packet, key, first_review, second_review)
    if basis != expected_basis or adjudication_packet != expected_packet:
        raise ValueError("adjudication inputs differ from frozen disagreement package")
    entries, authors = _context(episodes, packet, key)
    first, first_ids = _sheet(first_review, entries, authors)
    second, second_ids = _sheet(second_review, entries, authors | first_ids)
    disagreement_ids = {row["review_id"] for row in expected_packet["entries"]}
    final, _ = _sheet(resolutions, disagreement_ids, authors | first_ids | second_ids, adjudication=True)
    resolved = []
    for rid in entries:
        item = final[rid] if rid in final else first[rid]
        resolved.append({f: item[f] for f in ("review_id", "reviewer_id", *REVIEW_FIELDS)})
    # The canonical scorer retains every original gate and paired comparison.
    result = reconcile(episodes, packet, key, resolved)
    from .reviewer_choice_sensitivity import review_sensitivity
    final_by_id={row["review_id"]:row for row in resolved}
    result["reviewer_choice_sensitivity"]=review_sensitivity(episodes,key["mapping"],first,second,final_by_id)
    result["multi_reviewer_review"] = {
        "schema": FORMAT, "agreement_before_adjudication": agreement,
        "resolved_disagreement_entries": len(final), "all_entries_resolved": True,
        "basis": expected_basis, "resolution_sheet_sha256": _digest(resolutions),
        "individual_reviewer_product_decisions": {
            "first": reconcile(episodes, packet, key, list(first.values()))["product_decision"],
            "second": reconcile(episodes, packet, key, list(second.values()))["product_decision"],
        },
        "reviewer_identity_independently_authenticated": False,
        "adjudication_is_independent_business_truth_verification": False,
    }
    return result


def _load(path):
    with path.open("rb") as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds offline review size limit")
    return json.loads(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare-adjudication")
    score = sub.add_parser("score-adjudicated")
    for command in (prep, score):
        for name in ("episodes", "packet", "key", "first-review", "second-review"):
            command.add_argument("--"+name, type=Path, required=True)
    for name in ("review-dir", "private-dir"):
        prep.add_argument("--"+name, type=Path, required=True)
    prep.add_argument("--confirm-share-disagreements", action="store_true", required=True)
    for name in ("adjudication-packet", "basis", "resolutions", "out"):
        score.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.episodes.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("episode input exceeds offline review size limit")
    inputs = (_rows(args.episodes), _load(args.packet), _load(args.key),
              _load(args.first_review), _load(args.second_review))
    if args.command == "prepare-adjudication":
        public, private = args.review_dir.resolve(), args.private_dir.resolve()
        if public == private or public in private.parents or private in public.parents or public.exists() or private.exists():
            parser.error("choose separate new, non-nested review and private directories")
        packet, basis, agreement = prepare_adjudication(*inputs)
        private.mkdir(mode=0o700)
        _write_new(private / "basis.json", basis)
        _write_new(private / "agreement.json", agreement)
        public.mkdir(mode=0o700)
        _write_new(public / "disagreements.json", packet)
        _write_new(public / "resolution-template.json", adjudication_template(packet))
    else:
        result = score_adjudicated(*inputs, _load(args.adjudication_packet),
                                   _load(args.basis), _load(args.resolutions))
        _write_new(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
