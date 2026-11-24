"""Offline, review-only event assignment queue for an authorized work-resumption response.

This module does not fetch history or authorize joins. Input must be an already
authorized response; callers must protect its event IDs under the original grant.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

MAX_PAIRS = 24
MAX_EVENTS_PER_PAIR = 8
MAX_SESSIONS_PER_PAIR = 8


def _event_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("evidence_event_ids must be a list")
    if len(value) > MAX_EVENTS_PER_PAIR:
        raise ValueError("unbounded evidence list")
    if any(not isinstance(x, str) or not x or len(x) > 120 for x in value):
        raise ValueError("invalid event ID")
    # Duplicate source labels can hide conflicting provenance in an export.
    # Never normalize malformed evidence into apparently independent support.
    if len(set(value)) != len(value):
        raise ValueError("duplicate evidence event ID")
    return sorted(value)


# History-access modes that may produce a review queue; anything else is denied.
REVIEWABLE_ACCESS = ("all_saved", "current_run")


def _session_window(value: Any) -> str:
    """Canonicalize a bounded session instant; refuse forged or ambiguous labels."""
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("invalid earlier session timestamp")
    try:
        if len(value) == 10:
            # Legacy date-only sessions remain distinct from timestamped sessions.
            return "earlier:" + date.fromisoformat(value).isoformat()
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("timezone required")
        return "earlier:" + instant.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid earlier session timestamp") from exc


def _truncation_flag(container: dict[str, Any], name: str) -> bool:
    """Fail closed on malformed completeness metadata instead of truthiness coercion."""
    value = container.get(name, False)
    if type(value) is not bool:
        raise ValueError(f"invalid {name} completeness flag")
    return value


def build_review_queue(response: dict[str, Any]) -> dict[str, Any]:
    """Create deterministic event-level review decisions, never confirmed cases.

    An event attached to multiple resource pairs is ambiguous, not silently
    assigned to either pair. Earlier and current windows remain distinct.
    """
    if not isinstance(response, dict):
        raise ValueError("response must be an object")
    if response.get("derived") is not True or response.get("authoritative") is not False:
        raise ValueError("expected derived non-authoritative resumption response")
    # Navigation components are inspection aids, never proof of a shared case.
    # Reject contradictory authority claims instead of scoring a misleading
    # response whose top-level flag says non-authoritative.
    groups = response.get("observed_case_groups", [])
    if not isinstance(groups, list) or len(groups) > MAX_PAIRS:
        raise ValueError("invalid or unbounded provisional case groups")
    for group in groups:
        if not isinstance(group, dict) or group.get("proves_same_case") is not False:
            raise ValueError("provisional group must not assert case identity")
        # The digest includes navigation evidence. Bound untrusted records
        # before hashing so forged metadata cannot exhaust review requests.
        try:
            encoded = json.dumps(group, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("invalid provisional group evidence") from exc
        if len(encoded) > 65536:
            raise ValueError("unbounded provisional group evidence")
    pairs = response.get("observed_pair_event_assignments")
    if not isinstance(pairs, list) or len(pairs) > MAX_PAIRS:
        raise ValueError("missing or unbounded pair assignments")
    # Offline JSON is never proof of a current grant. Online callers must also
    # revalidate authorization/revocation before generating a review response.
    if response.get("saved_history_access") not in REVIEWABLE_ACCESS:
        raise ValueError("no recognized saved-history access")
    coverage = response.get("coverage", {})
    if not isinstance(coverage, dict):
        raise ValueError("invalid coverage metadata")
    indexed: dict[tuple[str, str], set[str]] = defaultdict(set)
    event_windows: dict[str, str] = {}
    def add_evidence(window: str, event_id: str, pair_key: str) -> None:
        # A source event cannot independently corroborate different windows.
        previous = event_windows.setdefault(event_id, window)
        if previous != window:
            raise ValueError("event ID reused across session windows")
        indexed[(window, event_id)].add(pair_key)
    # Validate *every* flag before aggregating. Short-circuiting with "or"
    # otherwise hides malformed later flags whenever an earlier flag is true.
    identity_timelines = response.get("identity_timelines", [])
    if not isinstance(identity_timelines, list) or len(identity_timelines) > 256:
        raise ValueError("invalid or unbounded identity timelines")
    # Inspection exposes at most twelve timelines. Bound each source record
    # before digesting it, including records outside the visible HTTP cap.
    for timeline in identity_timelines:
        if not isinstance(timeline, dict):
            raise ValueError("invalid identity timeline evidence")
        try:
            encoded_timeline = json.dumps(timeline, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("invalid identity timeline evidence") from exc
        if len(encoded_timeline) > 65536:
            raise ValueError("unbounded identity timeline evidence")
    # Continuity is visible only up to the HTTP inspection cap. An adapter
    # omitting the truncation flag must not allow scoring partial evidence.
    continuity = response.get("observed_group_continuity", [])
    if not isinstance(continuity, list) or len(continuity) > 256:
        raise ValueError("invalid or unbounded group continuity")
    for record in continuity:
        if not isinstance(record, dict):
            raise ValueError("invalid group continuity evidence")
        try:
            encoded = json.dumps(record, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("invalid group continuity evidence") from exc
        if len(encoded) > 65536:
            raise ValueError("unbounded group continuity evidence")
    # A visible timeline can itself omit earlier episodes or source events.
    # Treat nested omissions as incomplete review context, not a complete cohort.
    nested_timeline_truncated = False
    for timeline in identity_timelines:
        nested_timeline_truncated |= _truncation_flag(timeline, "episodes_truncated")
        episodes = timeline.get("episodes", [])
        if not isinstance(episodes, list) or len(episodes) > 8:
            raise ValueError("invalid or unbounded identity timeline episodes")
        for episode in episodes:
            if not isinstance(episode, dict):
                raise ValueError("invalid identity timeline episode")
            nested_timeline_truncated |= _truncation_flag(episode, "evidence_truncated")
            # The engine may omit interruption bursts even when every
            # retained burst has complete event evidence.
            nested_timeline_truncated |= _truncation_flag(episode, "segments_truncated")
            # An interruption segment may omit evidence independently of its episode.
            # Do not certify partial nested provenance as a complete cohort.
            segments = episode.get("segments", [])
            if not isinstance(segments, list) or len(segments) > 8:
                raise ValueError("invalid or unbounded identity timeline segments")
            for segment in segments:
                if not isinstance(segment, dict):
                    raise ValueError("invalid identity timeline segment")
                nested_timeline_truncated |= _truncation_flag(segment, "evidence_truncated")
    continuity_flag = _truncation_flag(response, "observed_group_continuity_truncated")
    identity_timelines_flag = _truncation_flag(response, "identity_timelines_truncated")
    continuity_truncated = continuity_flag or len(continuity) > 24
    identity_timelines_truncated = identity_timelines_flag or len(identity_timelines) > 12
    completeness_flags = (
        identity_timelines_truncated,
        nested_timeline_truncated,
        _truncation_flag(response, "observed_pair_event_assignments_truncated"),
        continuity_truncated,
        _truncation_flag(response, "observed_case_groups_truncated"),
        _truncation_flag(coverage, "scan_truncated"),
    )
    # Returned-count metadata is reviewer-visible provenance. If supplied by
    # an API adapter, refuse inconsistent counts rather than silently trusting
    # a forged or stale claim about how much evidence was actually displayed.
    # Offline engine responses may omit these presentation-only fields.
    for collection, cap in (
        ("identity_timelines", 12),
        ("observed_pair_event_assignments", 24),
        ("observed_group_continuity", 24),
        ("observed_case_groups", 24),
    ):
        count_key = collection + "_returned_count"
        if count_key in response:
            value = response[count_key]
            records = response.get(collection, [])
            if not isinstance(records, list):
                raise ValueError("invalid " + collection)
            if type(value) is not int or value != min(len(records), cap):
                raise ValueError("inconsistent " + count_key)
    incomplete = any(completeness_flags)
    seen_pair_keys: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, dict) or pair.get("assignment_status") != "observed_same_event_pair" or pair.get("proves_same_case") is not False:
            raise ValueError("unsupported pair assignment")
        pointers = pair.get("resource_pointers")
        if not isinstance(pointers, list) or len(pointers) != 2:
            raise ValueError("pair must have exactly two pointers")
        identities = []
        for pointer in pointers:
            if not isinstance(pointer, dict):
                raise ValueError("invalid pointer")
            identity = tuple(pointer.get(k) for k in ("provider", "resource_kind", "resource_ref"))
            if any(not isinstance(v, str) or not v or len(v) > 256 for v in identity):
                raise ValueError("invalid pointer identity")
            identities.append(identity)
        if identities[0] == identities[1]:
            raise ValueError("self-pair is not co-reference")
        pair_key = json.dumps(sorted(identities), separators=(",", ":"))
        # A repeated pair record can carry conflicting event provenance.
        # Refuse duplicates instead of silently unioning their evidence.
        if pair_key in seen_pair_keys:
            raise ValueError("duplicate resource pair assignment")
        seen_pair_keys.add(pair_key)
        for event_id in _event_ids(pair.get("current_evidence_event_ids")):
            add_evidence("current", event_id, pair_key)
        earlier = pair.get("earlier_corroborations", [])
        if not isinstance(earlier, list) or len(earlier) > MAX_SESSIONS_PER_PAIR:
            raise ValueError("unbounded earlier corroborations")
        seen_earlier_windows: set[str] = set()
        for session in earlier:
            if not isinstance(session, dict) or "session_started_at" not in session:
                raise ValueError("invalid earlier session")
            window = _session_window(session["session_started_at"])
            if window in seen_earlier_windows:
                raise ValueError("duplicate earlier session window")
            seen_earlier_windows.add(window)
            for event_id in _event_ids(session.get("evidence_event_ids")):
                add_evidence(window, event_id, pair_key)
            incomplete |= _truncation_flag(session, "evidence_truncated")
        pair_flags = (
            _truncation_flag(pair, "current_evidence_truncated"),
            _truncation_flag(pair, "earlier_sessions_truncated"),
        )
        incomplete |= any(pair_flags)
    rows = []
    # Chronological: earlier sessions by their start, then the current window.
    def _order(item):
        (window, event_id), _ = item
        return (0, window, event_id) if window.startswith("earlier:") else (1, window, event_id)
    for (window, event_id), candidates in sorted(indexed.items(), key=_order):
        rows.append({"window": window, "event_id": event_id,
                     "candidate_resource_pairs": [json.loads(p) for p in sorted(candidates)],
                     "review_status": "ambiguous_multiple_pairs" if len(candidates) > 1 else "observed_pair_needs_review",
                     "confirmed_case": False})
    # Pair-only evidence does not enumerate every event in the source scan.
    # Even a complete scan cannot establish that absent events were unassigned.
    # Bind the fingerprint to access mode and completeness, not only rows.
    # The same event list under a changed grant mode or truncated scan must
    # never be mistaken for the same review basis.
    fingerprint_basis = {
        "format": "openworkgraph.reconstruction-review.v2",
        "saved_history_access": response["saved_history_access"],
        "coverage_incomplete": incomplete,
        # Bind the observed scan and current session too: an unchanged set of
        # surfaced pairs under a different observation boundary is not the
        # same review basis. This metadata is produced by the live engine.
        "coverage": coverage,
        "current_session": response.get("current_session"),
        "session_gap_minutes": response.get("session_gap_minutes"),
        # Continuity is part of the reviewer-visible evidence basis. A changed
        # group linkage must invalidate old labels even when pair rows match.
        "observed_group_continuity": response.get("observed_group_continuity"),
        "observed_group_continuity_truncated": continuity_truncated,
        # The reviewer also sees the current connected-component navigation
        # groups. Their membership and truncation are part of the evidence
        # basis even when direct pair assignments remain unchanged.
        "observed_case_groups": response.get("observed_case_groups"),
        "observed_case_groups_truncated": response.get("observed_case_groups_truncated"),
        # The event-level rows are a projection: they omit source pair metadata
        # such as confidence, interruption context, and corroboration detail.
        # A reviewer must not replay a label after that source evidence changes.
        "observed_pair_event_assignments": pairs,
        # Identity timelines are shown alongside the review queue by the live
        # inspection API. Their provenance is part of the reviewer decision
        # context even when the event/pair projection is unchanged.
        "identity_timelines": response.get("identity_timelines"),
        "identity_timelines_truncated": identity_timelines_truncated,
        "event_reviews": rows,
    }
    # All reviewer-visible context participates in the evidence binding. Reject
    # malformed or excessive context before digesting; otherwise untrusted scan
    # metadata can cause an uncaught serialization failure or resource exhaustion.
    try:
        # Validate Unicode scalar values without changing the canonical digest's
        # historical ASCII-escaped serialization (saved review compatibility).
        json.dumps(fingerprint_basis, sort_keys=True, separators=(",", ":"),
                   allow_nan=False, ensure_ascii=False).encode("utf-8")
        evidence_bytes = json.dumps(
            fingerprint_basis, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid review evidence context") from exc
    if len(evidence_bytes) > 1048576:
        raise ValueError("unbounded review evidence context")
    evidence_digest = hashlib.sha256(evidence_bytes).hexdigest()
    return {"evidence_sha256": evidence_digest, "event_reviews": rows, "event_reviews_count": len(rows),
            "ambiguous_event_reviews_count": sum(row["review_status"] == "ambiguous_multiple_pairs" for row in rows),
            "single_pair_event_reviews_count": sum(row["review_status"] == "observed_pair_needs_review" for row in rows),
            "coverage_incomplete": incomplete,
            "unlisted_events_are_unassigned": False,
            "no_case_identity_inferred": True, "requires_human_review": True}



def score_review_annotations(response: dict[str, Any], annotations: list[dict[str, Any]]) -> dict[str, Any]:
    """Score explicit event/pair labels without treating absent events as negatives.

    This is a bounded reviewer-side calculation, NOT a claim of held-out quality.
    The caller must reauthorize the source response and independently establish
    annotation provenance, cohort independence, and grant validity.
    """
    queue = build_review_queue(response)
    if queue["coverage_incomplete"]:
        raise ValueError("incomplete source evidence cannot be scored")
    if not isinstance(annotations, list) or not annotations or len(annotations) > 192:
        raise ValueError("annotations must be a nonempty bounded list")
    observed = {(row["window"], row["event_id"]): row for row in queue["event_reviews"]}
    if not observed:
        raise ValueError("no surfaced review events to score")
    labels: dict[tuple[str, str], set[str]] = {}
    for annotation in annotations:
        if not isinstance(annotation, dict):
            raise ValueError("invalid annotation")
        # Untrusted JSON may contain arrays/objects in identifier fields. Reject
        # them explicitly rather than raising TypeError during dict lookup and
        # turning an invalid review request into an HTTP 500.
        window, event_id = annotation.get("window"), annotation.get("event_id")
        if not isinstance(window, str) or not isinstance(event_id, str):
            raise ValueError("invalid annotated event identity")
        key = (window, event_id)
        if key not in observed or key in labels:
            raise ValueError("unknown or duplicate annotated event")
        expected = annotation.get("true_resource_pairs")
        if not isinstance(expected, list) or len(expected) > MAX_PAIRS:
            raise ValueError("invalid ground truth pairs")
        canonical: set[str] = set()
        for identities in expected:
            if not isinstance(identities, list) or len(identities) != 2:
                raise ValueError("ground truth pair must have two identities")
            pair = []
            for identity in identities:
                if not isinstance(identity, list) or len(identity) != 3 or any(
                    not isinstance(part, str) or not part or len(part) > 256 for part in identity
                ):
                    raise ValueError("invalid ground truth identity")
                pair.append(identity)
            if pair[0] == pair[1]:
                raise ValueError("self-pair ground truth")
            canonical.add(json.dumps(sorted(pair), separators=(",", ":")))
        if len(canonical) != len(expected):
            raise ValueError("duplicate ground truth pair")
        labels[key] = canonical
    # A subset selected by the reviewer can inflate accuracy. Require an
    # explicit label (including an empty pair list) for every surfaced event.
    # Even then, unseen events remain outside this pair-only evaluation.
    if len(labels) != len(observed):
        raise ValueError("all surfaced review events require explicit labels")
    tp = fp = fn = ambiguous = 0
    # Separately count explicitly negative (no true pair) events. These are
    # interruption/rejection probes, not verified cross-case contamination.
    negative_events = negative_events_with_false_links = 0
    positive_events = positive_events_with_false_links = 0
    false_positive_events = false_negative_events = 0
    # Window-level counts reveal regressions hidden by aggregate accuracy.
    by_window: dict[str, dict[str, int]] = {}
    by_ambiguity: dict[str, dict[str, int]] = {
        "ambiguous_multiple_pairs": {"tp": 0, "fp": 0, "fn": 0, "events": 0},
        "single_pair": {"tp": 0, "fp": 0, "fn": 0, "events": 0},
    }
    disagreements: list[dict[str, Any]] = []
    for key, true_pairs in labels.items():
        row = observed[key]
        predicted = {json.dumps(pair, separators=(",", ":")) for pair in row["candidate_resource_pairs"]}
        correct, extra, missed = len(predicted & true_pairs), len(predicted - true_pairs), len(true_pairs - predicted)
        tp += correct
        fp += extra
        fn += missed
        false_positive_events += bool(extra)
        false_negative_events += bool(missed)
        if not true_pairs:
            negative_events += 1
            negative_events_with_false_links += bool(extra)
        else:
            # A correctly linked positive event can still leak into another case.
            # Negative-only probes cannot detect this failure mode.
            positive_events += 1
            positive_events_with_false_links += bool(extra)
        ambiguous += row["review_status"] == "ambiguous_multiple_pairs"
        if extra or missed:
            # Exact event/window provenance lets an authorized reviewer inspect
            # a disagreement without inferring a case from transitive groups.
            # Bounded by the mandatory complete annotation set (<=192 rows).
            disagreements.append({
                "window": key[0], "event_id": key[1],
                "false_positive_resource_pairs": [json.loads(p) for p in sorted(predicted - true_pairs)],
                "false_negative_resource_pairs": [json.loads(p) for p in sorted(true_pairs - predicted)],
                "ambiguous_multiple_pairs": row["review_status"] == "ambiguous_multiple_pairs",
            })
        counts = by_window.setdefault(key[0], {"tp": 0, "fp": 0, "fn": 0, "events": 0})
        counts["tp"] += correct
        counts["fp"] += extra
        counts["fn"] += missed
        counts["events"] += 1
        counts["false_link_events"] = counts.get("false_link_events", 0) + bool(extra)
        cohort = by_ambiguity["ambiguous_multiple_pairs" if row["review_status"] == "ambiguous_multiple_pairs" else "single_pair"]
        cohort["tp"] += correct
        cohort["fp"] += extra
        cohort["fn"] += missed
        cohort["events"] += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    # Report window-level quality as rates as well as counts. Aggregated micro
    # scores can conceal an entirely failed earlier-session reconstruction.
    per_window_metrics = {}
    for window, counts in sorted(by_window.items()):
        wp = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
        wr = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
        per_window_metrics[window] = {
            **counts, "precision": wp, "recall": wr,
            "false_link_event_rate": counts["false_link_events"] / counts["events"],
            "f1": 2 * wp * wr / (wp + wr) if wp + wr else 0.0,
        }
    ambiguity_metrics = {}
    for cohort_name, counts in by_ambiguity.items():
        cp = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
        cr = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
        ambiguity_metrics[cohort_name] = {
            **counts, "precision": cp, "recall": cr,
            "f1": 2 * cp * cr / (cp + cr) if cp + cr else 0.0,
        }
    return {
        "evidence_sha256": queue["evidence_sha256"],
        "annotated_events": len(labels), "source_review_events": len(observed),
        "unannotated_events": len(observed) - len(labels),
        "ambiguous_annotated_events": ambiguous,
        "true_positive_pairs": tp, "false_positive_pairs": fp, "false_negative_pairs": fn,
        "false_positive_events": false_positive_events,
        "false_link_event_rate": false_positive_events / len(labels),
        "false_negative_events": false_negative_events,
        "explicit_positive_events": positive_events,
        "explicit_positive_events_with_false_links": positive_events_with_false_links,
        "explicit_positive_false_link_rate": (
            positive_events_with_false_links / positive_events if positive_events else None
        ),
        "explicit_negative_events": negative_events,
        "explicit_negative_events_with_false_links": negative_events_with_false_links,
        "explicit_negative_false_link_rate": (
            negative_events_with_false_links / negative_events if negative_events else None
        ),
        "explicit_negative_metrics_are_reviewer_labels_not_held_out": True,
        "precision": precision, "recall": recall,
        "per_window_counts": {window: by_window[window] for window in sorted(by_window)},
        "per_window_metrics": per_window_metrics,
        "per_ambiguity_metrics": ambiguity_metrics,
        "event_disagreements": sorted(disagreements, key=lambda item: (item["window"], item["event_id"])),
        "event_disagreements_are_review_evidence_not_confirmed_cases": True,
        "cross_case_contamination_measured": False,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "evaluation_scope": "all_surfaced_event_pair_evidence_only",
        "missed_source_events_measured": False,
        "complete_case_recall_measured": False,
        "held_out_verified": False, "independent_annotation_verified": False,
        "real_work_accuracy_claim": False, "confirmed_case": False,
    }


def score_bound_review_annotations(response: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    """Reject stale/replayed reviewer labels from a different evidence export.

    The digest binds the exact surfaced evidence, not authorization or reviewer
    identity. Online callers must separately check live grants and provenance.
    """
    if not isinstance(envelope, dict) or set(envelope) != {"evidence_sha256", "annotations"}:
        raise ValueError("annotation envelope requires evidence_sha256 and annotations")
    digest = envelope["evidence_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid evidence digest")
    actual = build_review_queue(response)["evidence_sha256"]
    if not hmac.compare_digest(digest, actual):
        raise ValueError("annotation evidence digest mismatch")
    return score_review_annotations(response, envelope["annotations"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Review exact observed event-to-resource-pair evidence offline")
    parser.add_argument("input", help="Authorized work-resumption response JSON, or - for stdin")
    parser.add_argument("--annotations", help="Explicit reviewer labels JSON; scoring remains non-held-out")
    parser.add_argument("--source-labels", help="Explicit complete source-event inventory; not automatically held-out")
    args = parser.parse_args()
    if args.annotations and args.source_labels:
        parser.error("--annotations and --source-labels are mutually exclusive")
    try:
        if args.input == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.input, encoding="utf-8") as stream:
                payload = json.load(stream)
        if args.annotations or args.source_labels:
            labels_file = args.annotations or args.source_labels
            if args.input != "-" and os.path.samefile(labels_file, args.input):
                raise ValueError("source and annotation files must be distinct")
            with open(labels_file, encoding="utf-8") as stream:
                labels = json.load(stream)
            if args.source_labels:
                if not isinstance(labels, dict) or set(labels) != {"evidence_sha256", "source_events"}:
                    raise ValueError("source cohort envelope requires evidence_sha256 and source_events")
                digest = labels["evidence_sha256"]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("invalid evidence digest")
                if not hmac.compare_digest(digest, build_review_queue(payload)["evidence_sha256"]):
                    raise ValueError("source cohort evidence digest mismatch")
                output = score_source_event_cohort(payload, labels["source_events"])
            else:
                output = score_bound_review_annotations(payload, labels)
        else:
            output = build_review_queue(payload)
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"reconstruction review refused: {exc}", file=sys.stderr)
        return 2



def score_source_event_cohort(response: dict[str, Any], source_labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure a complete, externally labeled source-event cohort.

    Unlike reviewer scoring, the denominator includes events with no surfaced
    reconstruction assignment. Labels MUST be prepared independently before
    predictions are inspected; this function cannot authenticate that fact.
    The caller must also verify that the source-event inventory is exhaustive.
    No automatic held-out or real-world accuracy claim is made.
    """
    queue = build_review_queue(response)
    if queue["coverage_incomplete"]:
        raise ValueError("truncated reconstruction evidence cannot be evaluated")
    if not isinstance(source_labels, list) or not source_labels or len(source_labels) > 4096:
        raise ValueError("source event labels must be a nonempty bounded list")
    predictions = {(row["window"], row["event_id"]): {
        json.dumps(pair, separators=(",", ":")) for pair in row["candidate_resource_pairs"]
    } for row in queue["event_reviews"]}
    truth: dict[tuple[str, str], set[str]] = {}
    # An event is one source observation, not a separate observation per window.
    # Reject cross-window duplicates rather than inflating the evaluation cohort.
    event_windows: dict[str, str] = {}
    for label in source_labels:
        if not isinstance(label, dict):
            raise ValueError("invalid source event label")
        window, event_id = label.get("window"), label.get("event_id")
        if (not isinstance(window, str) or not window or len(window) > 128
                or not isinstance(event_id, str) or not event_id or len(event_id) > 120):
            raise ValueError("invalid source event identity")
        # Unsurfaced earlier observations must still use canonical UTC windows.
        # Fabricated session labels must not inflate false-negative counts.
        if window != "current":
            if not window.startswith("earlier:"):
                raise ValueError("noncanonical source event window")
            try:
                canonical_window = _session_window(window[len("earlier:"):])
            except ValueError as exc:
                raise ValueError("noncanonical source event window") from exc
            if canonical_window != window:
                raise ValueError("noncanonical source event window")
        previous_window = event_windows.setdefault(event_id, window)
        if previous_window != window:
            raise ValueError("source event ID reused across windows")
        key = (window, event_id)
        if key in truth:
            raise ValueError("duplicate source event label")
        pairs = label.get("true_resource_pairs")
        if not isinstance(pairs, list) or len(pairs) > MAX_PAIRS:
            raise ValueError("invalid source ground truth")
        canonical = set()
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("invalid source pair")
            if any(not isinstance(identity, list) or len(identity) != 3 or any(
                not isinstance(part, str) or not part or len(part) > 256 for part in identity
            ) for identity in pair) or pair[0] == pair[1]:
                raise ValueError("invalid source pair identity")
            canonical.add(json.dumps(sorted(pair), separators=(",", ":")))
        if len(canonical) != len(pairs):
            raise ValueError("duplicate source ground truth pair")
        truth[key] = canonical
    if not set(predictions).issubset(truth):
        raise ValueError("source inventory omits surfaced prediction events")
    tp = fp = fn = missed_events = negative_events = contaminated_negative_events = false_link_events = 0
    positive_events = contaminated_positive_events = 0
    per_window: dict[str, dict[str, int]] = {}
    # Separate ambiguous multi-pair predictions from single-pair predictions
    # and source events the engine never surfaced. This exposes contamination
    # hidden by aggregate micro scores without claiming independent labels.
    per_prediction_status: dict[str, dict[str, int]] = {
        name: {"events": 0, "tp": 0, "fp": 0, "fn": 0, "false_link_events": 0}
        for name in ("ambiguous_multiple_pairs", "single_pair", "no_prediction")
    }
    disagreements: list[dict[str, Any]] = []
    for key, expected in sorted(truth.items()):
        predicted = predictions.get(key, set())
        correct, extra, missing = predicted & expected, predicted - expected, expected - predicted
        tp += len(correct)
        fp += len(extra)
        fn += len(missing)
        false_link_events += bool(extra)
        status = ("no_prediction" if key not in predictions else
                  "ambiguous_multiple_pairs" if len(predicted) > 1 else "single_pair")
        status_counts = per_prediction_status[status]
        status_counts["events"] += 1
        status_counts["tp"] += len(correct)
        status_counts["fp"] += len(extra)
        status_counts["fn"] += len(missing)
        status_counts["false_link_events"] += bool(extra)
        missed_events += bool(expected and not predicted)
        negative_events += not expected
        positive_events += bool(expected)
        contaminated_positive_events += bool(expected and extra)
        contaminated_negative_events += bool(not expected and predicted)
        counts = per_window.setdefault(key[0], {"events": 0, "tp": 0, "fp": 0, "fn": 0,
                                                 "negative_events": 0, "negative_false_link_events": 0, "false_link_events": 0,
                                                 "positive_events": 0, "positive_false_link_events": 0})
        counts["events"] += 1
        counts["tp"] += len(correct)
        counts["fp"] += len(extra)
        counts["fn"] += len(missing)
        counts["false_link_events"] += bool(extra)
        counts["positive_events"] += bool(expected)
        counts["positive_false_link_events"] += bool(expected and extra)
        counts["negative_events"] += not expected
        counts["negative_false_link_events"] += bool(not expected and predicted)
        if extra or missing:
            disagreements.append({
                "window": key[0], "event_id": key[1],
                "false_positive_resource_pairs": [json.loads(p) for p in sorted(extra)],
                "false_negative_resource_pairs": [json.loads(p) for p in sorted(missing)],
                "explicit_negative_event": not expected,
                "prediction_missing": key not in predictions,
            })
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    per_window_metrics = {}
    for window, counts in sorted(per_window.items()):
        wp = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
        wr = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
        per_window_metrics[window] = {
            **counts,
            "precision": wp, "recall": wr,
            "f1": 2 * wp * wr / (wp + wr) if wp + wr else 0.0,
            "source_event_false_link_rate": counts["false_link_events"] / counts["events"],
            "positive_false_link_rate": (
                counts["positive_false_link_events"] / counts["positive_events"]
                if counts["positive_events"] else None
            ),
            "negative_false_link_rate": (
                counts["negative_false_link_events"] / counts["negative_events"]
                if counts["negative_events"] else None
            ),
        }
    return {
        "source_events_labeled": len(truth),
        "source_events_without_surfaced_predictions": len(set(truth) - set(predictions)),
        "positive_source_events_without_predictions": missed_events,
        "true_positive_pairs": tp, "false_positive_pairs": fp, "false_negative_pairs": fn,
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "per_window_counts": per_window,
        "per_window_metrics": per_window_metrics,
        "per_prediction_status_metrics": {
            name: {
                **counts,
                "precision": counts["tp"] / (counts["tp"] + counts["fp"])
                if counts["tp"] + counts["fp"] else 0.0,
                "recall": counts["tp"] / (counts["tp"] + counts["fn"])
                if counts["tp"] + counts["fn"] else 0.0,
                "false_link_event_rate": (
                    counts["false_link_events"] / counts["events"] if counts["events"] else None
                ),
            }
            for name, counts in per_prediction_status.items()
        },
        "event_disagreements": disagreements,
        "event_disagreements_are_label_comparisons_not_confirmed_cases": True,
        "source_events_with_false_links": false_link_events,
        "source_event_false_link_rate": false_link_events / len(truth),
        "positive_source_events": positive_events,
        "positive_source_events_with_false_links": contaminated_positive_events,
        "positive_false_link_rate": (
            contaminated_positive_events / positive_events if positive_events else None
        ),
        "negative_source_events": negative_events,
        "negative_source_events_with_false_links": contaminated_negative_events,
        "negative_false_link_rate": (
            contaminated_negative_events / negative_events if negative_events else None
        ),
        "source_event_inventory_exhaustiveness_verified": False,
        "independent_labels_verified": False,
        "held_out_verified": False,
        "real_work_accuracy_claim": False,
        "evaluation_scope": "caller_supplied_source_event_inventory",
        "evidence_sha256": queue["evidence_sha256"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
