"""Offline blinded product-value review preparation and reconciliation.

Operator-supplied review material must already be approved and stripped of
condition-specific identifiers. No recording, model calls or source reads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import secrets
from pathlib import Path

from .product_value import ARMS, REVIEW_FIELDS, _index, _validated_review, report

SCHEMA = "owg.blinded-product-review.v1"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: nonempty string required")
    return value


def _rows(path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_new(path, value):
    # Exclusive creation prevents accidentally overwriting a frozen study artifact.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def prepare(episodes, runs, *, seed=None, id_factory=None):
    """Return (review packet, private key). No arm metadata in the packet.

    Each run must include completed-turn provenance and an explicitly
    operator-approved, condition-neutral review_text. Automatic blinding of
    free text is not possible; the operator must inspect all material.
    """
    truth = _index(episodes, label="ground truth")
    if set(runs) != set(ARMS):
        raise ValueError("exactly control, raw and goal arms required")
    indexed = {arm: _index(runs[arm], label=arm) for arm in ARMS}
    for arm in ARMS:
        if set(indexed[arm]) != set(truth):
            raise ValueError(f"{arm}: episode set mismatch")
    token = id_factory or (lambda: secrets.token_hex(16))
    entries, mapping = [], {}
    for episode_id, episode in sorted(truth.items()):
        prompt = _text(episode.get("prompt"), "episode prompt")
        states, models = set(), set()
        for arm in ARMS:
            row = indexed[arm][episode_id]
            if row.get("recorded_model_completion") is not True:
                raise ValueError(f"{arm}/{episode_id}: incomplete turn")
            if row.get("review_material_approved") is not True:
                raise ValueError(f"{arm}/{episode_id}: explicit material approval required")
            model = _text(row.get("model_id"), "model_id")
            state = _text(row.get("source_state_id"), "source_state_id")
            models.add(model)
            states.add(state)
            review_text = _text(row.get("review_text"), "review_text")
            review_id = _text(token(), "review ID")
            if review_id in mapping:
                raise ValueError("duplicate generated review ID")
            # Do not copy arbitrary source fields, tool names, paths or arm labels.
            entries.append({"review_id": review_id, "prompt": prompt,
                            "review_text": review_text})
            mapping[review_id] = {"episode_id": episode_id, "arm": arm,
                                  "model_id": model, "source_state_id": state}
        if len(states) != 1 or len(models) != 1:
            raise ValueError(f"{episode_id}: paired model/source state mismatch")
    # Seed controls order only, not the opaque IDs. Production IDs are random.
    (random.SystemRandom() if seed is None else random.Random(seed)).shuffle(entries)
    packet = {"schema": SCHEMA, "entries": entries,
              "limitations": "Operator-reviewed free text; blinding is not guaranteed."}
    key = {"schema": SCHEMA, "packet_digest": _digest(packet),
           "truth_digest": _digest(episodes), "mapping": mapping}
    return packet, key


def review_template(packet):
    """Null fields deliberately cannot pass as completed reviews."""
    return [{"review_id": entry["review_id"], "reviewer_id": None,
             **{field: None for field in REVIEW_FIELDS}}
            for entry in packet["entries"]]


def reconcile(episodes, packet, key, reviews):
    """Validate exact coverage and reconstruct inputs for the existing scorer."""
    if packet.get("schema") != SCHEMA or key.get("schema") != SCHEMA:
        raise ValueError("unsupported packet/key schema")
    if key.get("packet_digest") != _digest(packet):
        raise ValueError("review packet differs from frozen packet")
    if key.get("truth_digest") != _digest(episodes):
        raise ValueError("ground truth differs from frozen truth")
    mapping = key.get("mapping")
    if not isinstance(mapping, dict):
        raise ValueError("invalid private mapping")
    entries = packet.get("entries")
    if not isinstance(entries, list):
        raise ValueError("invalid packet entries")
    expected = set()
    for entry in entries:
        review_id = _text(entry.get("review_id"), "packet review ID")
        if review_id in expected:
            raise ValueError("duplicate packet review ID")
        expected.add(review_id)
    if expected != set(mapping):
        raise ValueError("packet and key coverage mismatch")
    indexed = {}
    for row in reviews:
        if not isinstance(row, dict):
            raise ValueError("review rows must be objects")
        review_id = _text(row.get("review_id"), "review ID")
        if review_id in indexed:
            raise ValueError("duplicate review ID")
        indexed[review_id] = row
    if set(indexed) != expected:
        raise ValueError("missing or unexpected review IDs")
    truth = _index(episodes, label="ground truth")
    out = {arm: [] for arm in ARMS}
    seen = set()
    for review_id, meta in mapping.items():
        arm, episode_id = meta.get("arm"), meta.get("episode_id")
        if arm not in out or episode_id not in truth or (arm, episode_id) in seen:
            raise ValueError("invalid or duplicate arm/episode binding")
        seen.add((arm, episode_id))
        row = indexed[review_id]
        reviewer = _text(row.get("reviewer_id"), "reviewer ID")
        # Reject an explicitly identified ground-truth author as sole reviewer.
        if reviewer == truth[episode_id].get("ground_truth_verified_by"):
            raise ValueError("reviewer must differ from ground-truth verifier")
        if not all(field in row for field in REVIEW_FIELDS):
            raise ValueError("incomplete review")
        for field in ("selected_resources", "selected_agent_runs"):
            values = row[field]
            if not isinstance(values, list) or any(
                    not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{field}: list of nonempty strings required")
            if len(set(values)) != len(values):
                raise ValueError(f"{field}: duplicate selected evidence")
        run = {"episode_id": episode_id, "recorded_model_completion": True,
               "model_id": _text(meta.get("model_id"), "model ID"),
               "source_state_id": _text(meta.get("source_state_id"), "source state"),
               "independent_review": {"reviewer_id": reviewer,
                                      **{f: row[f] for f in REVIEW_FIELDS}}}
        _validated_review(run, episode_id=episode_id, arm=arm)
        out[arm].append(run)
    if seen != {(arm, ep) for arm in ARMS for ep in truth}:
        raise ValueError("incomplete paired coverage")
    # Reuse the canonical scorer; do not implement a second acceptance threshold.
    return report(episodes, out["control"], out["raw"], out["goal"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--episodes", type=Path, required=True)
    for arm in ARMS:
        prep.add_argument("--" + arm, type=Path, required=True)
    prep.add_argument("--review-dir", type=Path, required=True)
    prep.add_argument("--private-dir", type=Path, required=True)
    prep.add_argument("--confirm-reviewed-material", action="store_true", required=True)
    score = commands.add_parser("score")
    for name in ("episodes", "packet", "key", "reviews", "out"):
        score.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        public, private = args.review_dir.resolve(), args.private_dir.resolve()
        if public == private or public in private.parents or private in public.parents:
            parser.error("review and private directories must be separate, non-nested paths")
        if public.exists() or private.exists():
            parser.error("output directories must not exist")
        packet, key = prepare(_rows(args.episodes),
                              {arm: _rows(getattr(args, arm)) for arm in ARMS})
        private.mkdir(mode=0o700)
        _write_new(private / "key.json", key)
        public.mkdir(mode=0o700)
        _write_new(public / "packet.json", packet)
        _write_new(public / "review-template.json", review_template(packet))
    else:
        def load(path):
            return json.loads(path.read_text(encoding="utf-8"))
        result = reconcile(_rows(args.episodes), load(args.packet), load(args.key),
                           load(args.reviews))
        _write_new(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
