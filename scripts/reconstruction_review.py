"""Local-only, opt-in CLI for reviewing uncertain OWG reconstruction membership.

No network access, provider writes, canonical event mutation or auto-approval.
Run from repository root: python -m scripts.reconstruction_review prepare ...
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from server.reconstruction_review_overlay import (
    BATCH_FORMAT, MAX_BATCH_SESSIONS, aggregate_review_scores,
    apply_review, prepare_review_packet, score_review,
)

_MAX_JSON = 8 * 1024 * 1024


def _read_json(path: str, *, max_bytes: int = _MAX_JSON) -> Any:
    with open(path, "rb") as source:
        raw = source.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("input exceeds the local review size limit")
    def refuse_constant(value: str) -> None:
        raise ValueError("nonfinite JSON constant is not supported")
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key is not supported")
            result[key] = value
        return result
    try:
        return json.loads(raw, parse_constant=refuse_constant,
                          object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid UTF-8 JSON input") from exc


def _write_private(path: str, document: dict[str, Any]) -> None:
    # Never silently replace an earlier review packet, annotation or report.
    # The operator chooses a local destination; the file is private at creation.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(document, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _score_batch(manifest: Any) -> dict[str, Any]:
    """Run the real linker and scorer on each explicitly selected local session."""
    if type(manifest) is not dict or set(manifest) != {"format", "sessions"}:
        raise ValueError("batch manifest must contain only format and sessions")
    if manifest["format"] != "openworkgraph.reconstruction-review-manifest.v1":
        raise ValueError("unsupported reconstruction batch manifest")
    sessions = manifest["sessions"]
    if type(sessions) is not list or not 2 <= len(sessions) <= MAX_BATCH_SESSIONS:
        raise ValueError("batch requires 2 to 24 sessions")
    reports = []
    for session in sessions:
        if type(session) is not dict or set(session) != {
            "events", "packet", "decisions", "truth",
        }:
            raise ValueError("each session needs events, packet, decisions and truth")
        if any(type(value) is not str or not 1 <= len(value) <= 4096
               for value in session.values()):
            raise ValueError("session file paths must be nonempty bounded strings")
        events = _read_json(session["events"])
        packet = _read_json(session["packet"], max_bytes=1024 * 1024)
        decisions = _read_json(session["decisions"], max_bytes=128 * 1024)
        truth = _read_json(session["truth"], max_bytes=1024 * 1024)
        # score_review rederives the current evidence, rejects stale/forged
        # packets and verifies complete independent labels before scoring.
        reports.append(score_review(events, packet, decisions, truth))
    return aggregate_review_scores(reports)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Human-reviewed, evidence-bound local reconstruction overlay"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "apply", "score", "score-batch"):
        command = sub.add_parser(name)
        if name == "score-batch":
            command.add_argument("--manifest", required=True,
                                 help="local list of independent annotated sessions")
        else:
            command.add_argument("--events", required=True,
                                 help="consented local JSON event array")
        command.add_argument("--output", required=True, help="new local private JSON file")
        if name in ("apply", "score"):
            command.add_argument("--packet", required=True)
            command.add_argument("--decisions", required=True)
        if name == "score":
            command.add_argument("--truth", required=True,
                                 help="independent complete local annotations")
    args = parser.parse_args(argv)
    try:
        if args.command == "score-batch":
            result = _score_batch(_read_json(args.manifest, max_bytes=128 * 1024))
        else:
            events = _read_json(args.events)
        if args.command == "prepare":
            result = prepare_review_packet(events)
        elif args.command != "score-batch":
            packet = _read_json(args.packet, max_bytes=1024 * 1024)
            decisions = _read_json(args.decisions, max_bytes=128 * 1024)
            if args.command == "apply":
                result = apply_review(events, packet, decisions)
            else:
                truth = _read_json(args.truth, max_bytes=1024 * 1024)
                result = score_review(events, packet, decisions, truth)
        _write_private(args.output, result)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Review refused: {exc}", file=sys.stderr)
        return 2
    print(f"Created private {args.command} result: {Path(args.output).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
