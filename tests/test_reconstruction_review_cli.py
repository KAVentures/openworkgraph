"""Adversarial review queue tests; synthetic fixtures are not assignment-F1 evidence."""
import unittest

from server.reconstruction_review_cli import build_review_queue, score_review_annotations


def pair(left, right, events, earlier=None, **extra):
    return {"resource_pointers": [
        {"provider": "github", "resource_kind": "issue", "resource_ref": left},
        {"provider": "local_file", "resource_kind": "file", "resource_ref": right}],
        "current_evidence_event_ids": events,
        "earlier_corroborations": earlier or [],
        "assignment_status": "observed_same_event_pair", "proves_same_case": False, **extra}


def payload(pairs):
    return {"derived": True, "authoritative": False, "saved_history_access": "all_saved",
            "observed_pair_event_assignments": pairs, "coverage": {"scan_truncated": False}}




class AnnotationCliTests(unittest.TestCase):
    def test_operator_scoring_entrypoint_uses_explicit_labels(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        from server.reconstruction_review_cli import main

        source = payload([pair("A", "F", ["e1"])])
        row = build_review_queue(source)["event_reviews"][0]
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"]}]
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.json"
            labels_path = Path(directory) / "labels.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            labels_path.write_text(json.dumps({"evidence_sha256": build_review_queue(source)["evidence_sha256"], "annotations": labels}), encoding="utf-8")
            output = StringIO()
            with patch("sys.argv", ["review", str(source_path), "--annotations", str(labels_path)]):
                with redirect_stdout(output):
                    self.assertEqual(main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["true_positive_pairs"], 1)
            self.assertFalse(result["held_out_verified"])
            self.assertFalse(result["real_work_accuracy_claim"])

    def test_operator_scoring_refuses_same_file_and_revoked_source(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from contextlib import redirect_stderr
        from io import StringIO
        from server.reconstruction_review_cli import main

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.json"
            labels_path = Path(directory) / "labels.json"
            source = payload([pair("A", "F", ["e1"])])
            source_path.write_text(json.dumps(source), encoding="utf-8")
            labels_path.write_text("[]", encoding="utf-8")
            with patch("sys.argv", ["review", str(source_path), "--annotations", str(source_path)]):
                with redirect_stderr(StringIO()):
                    self.assertEqual(main(), 2)
            source["saved_history_access"] = "revoked"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            with patch("sys.argv", ["review", str(source_path), "--annotations", str(labels_path)]):
                with redirect_stderr(StringIO()):
                    self.assertEqual(main(), 2)

    def test_operator_scoring_refuses_hardlink_to_source(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from contextlib import redirect_stderr
        from io import StringIO
        from server.reconstruction_review_cli import main

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.json"
            alias_path = Path(directory) / "labels.json"
            source_path.write_text(json.dumps(payload([pair("A", "F", ["e1"])])), encoding="utf-8")
            os.link(source_path, alias_path)
            with patch("sys.argv", ["review", str(source_path), "--annotations", str(alias_path)]):
                with redirect_stderr(StringIO()) as errors:
                    self.assertEqual(main(), 2)
            self.assertIn("must be distinct", errors.getvalue())

class PositiveEventContaminationMetricsTests(unittest.TestCase):
    def test_positive_false_link_detected_even_with_true_link_present(self):
        source = payload([pair("A", "F", ["shared"]), pair("B", "G", ["shared"])])
        row = build_review_queue(source)["event_reviews"][0]
        score = score_review_annotations(source, [{
            "window": row["window"], "event_id": row["event_id"],
            "true_resource_pairs": [row["candidate_resource_pairs"][0]],
        }])
        self.assertEqual(score["explicit_positive_events"], 1)
        self.assertEqual(score["explicit_positive_events_with_false_links"], 1)
        self.assertEqual(score["explicit_positive_false_link_rate"], 1.0)
        self.assertEqual(score["explicit_negative_events"], 0)
        self.assertIsNone(score["explicit_negative_false_link_rate"])
        self.assertFalse(score["held_out_verified"])
        self.assertFalse(score["cross_case_contamination_measured"])

    def test_negative_only_probe_does_not_inflate_positive_denominator(self):
        source = payload([pair("A", "F", ["negative"])])
        row = build_review_queue(source)["event_reviews"][0]
        score = score_review_annotations(source, [{
            "window": row["window"], "event_id": row["event_id"],
            "true_resource_pairs": [],
        }])
        self.assertEqual(score["explicit_positive_events"], 0)
        self.assertEqual(score["explicit_positive_events_with_false_links"], 0)
        self.assertIsNone(score["explicit_positive_false_link_rate"])
        self.assertEqual(score["explicit_negative_false_link_rate"], 1.0)


class TruncatedNavigationEvidenceTests(unittest.TestCase):
    def test_group_or_continuity_truncation_blocks_scoring(self):
        # Pair rows may be complete even when case navigation is not.
        # A reviewer must not score that partial evidence as a complete cohort.
        for flag in ("observed_case_groups_truncated",
                     "observed_group_continuity_truncated"):
            with self.subTest(flag=flag):
                source = payload([pair("A", "F", ["e1"])])
                source[flag] = True
                queue = build_review_queue(source)
                self.assertTrue(queue["coverage_incomplete"])
                row = queue["event_reviews"][0]
                labels = [{"window": row["window"], "event_id": row["event_id"],
                           "true_resource_pairs": row["candidate_resource_pairs"]}]
                with self.assertRaisesRegex(ValueError, "incomplete source evidence"):
                    score_review_annotations(source, labels)

    def test_hidden_continuity_records_force_incomplete_scoring(self):
        source = payload([pair("A", "F", ["e1"])])
        source["observed_group_continuity"] = [{"group_id": str(i)} for i in range(25)]
        queue = build_review_queue(source)
        self.assertTrue(queue["coverage_incomplete"])
        row = queue["event_reviews"][0]
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"]}]
        with self.assertRaisesRegex(ValueError, "incomplete source evidence"):
            score_review_annotations(source, labels)
        # An explicit false flag must not override the actual source length.
        source["observed_group_continuity_truncated"] = False
        self.assertTrue(build_review_queue(source)["coverage_incomplete"])

    def test_hidden_continuity_validation_is_fail_closed(self):
        source = payload([pair("A", "F", ["e1"])])
        for records in ([{"group_id": str(i)} for i in range(257)],
                        [{"group_id": str(i)} for i in range(24)] + [None],
                        [{"group_id": "x" * 65536}]):
            with self.subTest(size=len(records)):
                source["observed_group_continuity"] = records
                with self.assertRaisesRegex(ValueError, "continuity"):
                    build_review_queue(source)

    def test_over_cap_navigation_still_rejects_malformed_truncation_flags(self):
        # A large source list must not short-circuit validation of the
        # explicit completeness flag supplied by the authenticated adapter.
        for collection, flag in (
            ("observed_group_continuity", "observed_group_continuity_truncated"),
            ("identity_timelines", "identity_timelines_truncated"),
        ):
            with self.subTest(collection=collection):
                source = payload([pair("A", "F", ["e1"])])
                source[collection] = [{"group_id": str(i)} for i in range(25)]
                source[flag] = "false"
                with self.assertRaisesRegex(ValueError, "invalid " + flag):
                    build_review_queue(source)

    def test_nested_timeline_omissions_block_complete_scoring(self):
        for timeline in (
            {"episodes_truncated": True, "episodes": []},
            {"episodes_truncated": False, "episodes": [{"evidence_truncated": True}]},
            {"episodes": [{"evidence_truncated": False, "segments": [
                {"evidence_truncated": True}]}]},
            {"episodes": [{"evidence_truncated": False, "segments_truncated": True,
                           "segments": [{"evidence_truncated": False}]}]},
        ):
            with self.subTest(timeline=timeline):
                source = payload([pair("A", "F", ["e1"])])
                source["identity_timelines"] = [timeline]
                queue = build_review_queue(source)
                self.assertTrue(queue["coverage_incomplete"])
                row = queue["event_reviews"][0]
                labels = [{"window": row["window"], "event_id": row["event_id"],
                           "true_resource_pairs": row["candidate_resource_pairs"]}]
                with self.assertRaisesRegex(ValueError, "incomplete source evidence"):
                    score_review_annotations(source, labels)

    def test_malformed_nested_timeline_metadata_fails_closed(self):
        for timeline in (
            {"episodes_truncated": "false", "episodes": []},
            {"episodes": [{"evidence_truncated": 0}]},
            {"episodes": [None]},
            {"episodes": [{"segments": [{"evidence_truncated": "false"}]}]},
            {"episodes": [{"segments_truncated": "false", "segments": []}]},
            {"episodes": [{"segments": [None]}]},
            {"episodes": [{"segments": [{}] * 9}]},
            {"episodes": [{}] * 9},
        ):
            with self.subTest(timeline=timeline):
                source = payload([pair("A", "F", ["e1"])])
                source["identity_timelines"] = [timeline]
                with self.assertRaisesRegex(ValueError, "invalid"):
                    build_review_queue(source)

    def test_complete_navigation_remains_scoreable(self):
        source = payload([pair("A", "F", ["e1"])])
        source["observed_case_groups_truncated"] = False
        source["observed_group_continuity_truncated"] = False
        row = build_review_queue(source)["event_reviews"][0]
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"]}]
        self.assertEqual(score_review_annotations(source, labels)["true_positive_pairs"], 1)


class EvidenceBoundAnnotationTests(unittest.TestCase):
    def test_bound_annotations_accept_exact_export_and_reject_stale_export(self):
        from server.reconstruction_review_cli import score_bound_review_annotations
        source = payload([pair("A", "F", ["a"])])
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        envelope = {"evidence_sha256": queue["evidence_sha256"],
                    "annotations": [{"window": row["window"], "event_id": row["event_id"],
                                     "true_resource_pairs": row["candidate_resource_pairs"]}]}
        self.assertEqual(score_bound_review_annotations(source, envelope)["true_positive_pairs"], 1)
        changed = payload([pair("B", "G", ["a"])])
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            score_bound_review_annotations(changed, envelope)

    def test_changed_group_continuity_invalidates_review_labels(self):
        from server.reconstruction_review_cli import score_bound_review_annotations
        source = payload([pair("A", "F", ["e1"])])
        source["observed_group_continuity"] = [{"group_id": "g1", "evidence_event_ids": ["e1"]}]
        source["observed_group_continuity_truncated"] = False
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        envelope = {"evidence_sha256": queue["evidence_sha256"],
                    "annotations": [{"window": row["window"], "event_id": row["event_id"],
                                     "true_resource_pairs": row["candidate_resource_pairs"]}]}
        self.assertEqual(score_bound_review_annotations(source, envelope)["true_positive_pairs"], 1)
        changed = dict(source, observed_group_continuity=[
            {"group_id": "g2", "evidence_event_ids": ["e1"]}])
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            score_bound_review_annotations(changed, envelope)
        changed = dict(source, observed_group_continuity_truncated=True)
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            score_bound_review_annotations(changed, envelope)

    def test_unbound_or_invalid_annotation_envelopes_are_refused(self):
        from server.reconstruction_review_cli import score_bound_review_annotations
        source = payload([pair("A", "F", ["a"])])
        for envelope in ([], {}, {"annotations": []},
                         {"evidence_sha256": "x" * 64, "annotations": []},
                         {"evidence_sha256": build_review_queue(source)["evidence_sha256"],
                          "annotations": [], "reviewer": "unverified"}):
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                score_bound_review_annotations(source, envelope)


class ExplicitAnnotationScoreTests(unittest.TestCase):
    def test_empty_review_cohort_cannot_report_zero_f1(self):
        with self.assertRaisesRegex(ValueError, "no surfaced review events"):
            score_review_annotations(payload([]), [{"window": "current", "event_id": "fake", "true_resource_pairs": []}])

    def test_interleaved_cases_score_only_exact_annotated_event_pair(self):
        data = payload([pair("A", "F", ["a"]), pair("B", "G", ["b"])])
        review = build_review_queue(data)
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"]}
                  for row in review["event_reviews"]]
        score = score_review_annotations(data, labels)
        self.assertEqual((score["precision"], score["recall"], score["f1"]), (1.0, 1.0, 1.0))
        self.assertFalse(score["held_out_verified"])
        self.assertFalse(score["real_work_accuracy_claim"])

    def test_adversarial_interleaved_cases_and_unrelated_shared_event_expose_contamination(self):
        # Synthetic case truth is specified independently of the proposed
        # assignments: shared is an interruption, not evidence for either case.
        # This measures pair-level leakage on this fixture only, not held-out
        # accuracy or real-world cross-case contamination.
        data = payload([pair("A", "F", ["a1", "shared"]),
                        pair("B", "G", ["b1", "shared"])])
        rows = build_review_queue(data)["event_reviews"]
        truth = {
            "a1": [pair("A", "F", ["a1"])["resource_pointers"]],
            "b1": [pair("B", "G", ["b1"])["resource_pointers"]],
            "shared": [],
        }
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": [
                       [[p["provider"], p["resource_kind"], p["resource_ref"]] for p in pair_pointers]
                       for pair_pointers in truth[row["event_id"]]]}
                  for row in rows]
        score = score_review_annotations(data, labels)
        self.assertEqual((score["true_positive_pairs"], score["false_positive_pairs"],
                          score["false_negative_pairs"]), (2, 2, 0))
        self.assertAlmostEqual(score["precision"], 0.5)
        self.assertAlmostEqual(score["recall"], 1.0)
        self.assertAlmostEqual(score["f1"], 2 / 3)
        self.assertEqual(score["ambiguous_annotated_events"], 1)
        self.assertAlmostEqual(score["false_link_event_rate"], 1 / 3)
        self.assertEqual(score["per_window_metrics"]["current"]["false_link_events"], 1)
        self.assertAlmostEqual(score["per_window_metrics"]["current"]["false_link_event_rate"], 1 / 3)
        self.assertEqual([row["event_id"] for row in score["event_disagreements"]], ["shared"])
        self.assertFalse(score["cross_case_contamination_measured"])
        self.assertFalse(score["held_out_verified"])
        self.assertFalse(score["real_work_accuracy_claim"])

    def test_ambiguous_assignment_counts_false_positive_not_silent_case_merge(self):
        data = payload([pair("A", "F", ["shared"]), pair("B", "G", ["shared"])])
        row = build_review_queue(data)["event_reviews"][0]
        label = {"window": row["window"], "event_id": row["event_id"],
                 "true_resource_pairs": row["candidate_resource_pairs"][:1]}
        score = score_review_annotations(data, [label])
        self.assertEqual(score["ambiguous_annotated_events"], 1)
        self.assertEqual(score["false_positive_pairs"], 1)
        self.assertEqual(score["precision"], 0.5)
        self.assertFalse(score["confirmed_case"])

    def test_window_errors_expose_interleaved_false_pair_without_claiming_case_metric(self):
        earlier = [{"session_started_at": "2026-10-12T09:00:00Z", "evidence_event_ids": ["old"]}]
        data = payload([pair("A", "F", ["new"], earlier=earlier)])
        rows = build_review_queue(data)["event_reviews"]
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"] if row["event_id"] == "old" else []}
                  for row in rows]
        score = score_review_annotations(data, labels)
        self.assertEqual(score["per_window_counts"]["current"], {"tp": 0, "fp": 1, "fn": 0, "events": 1, "false_link_events": 1})
        self.assertEqual(score["per_window_counts"]["earlier:2026-10-12T09:00:00+00:00"],
                         {"tp": 1, "fp": 0, "fn": 0, "events": 1, "false_link_events": 0})
        self.assertFalse(score["cross_case_contamination_measured"])
        disagreements = score["event_disagreements"]
        self.assertEqual(len(disagreements), 1)
        self.assertEqual(disagreements[0]["event_id"], "new")
        self.assertEqual(len(disagreements[0]["false_positive_resource_pairs"]), 1)
        self.assertEqual(disagreements[0]["false_negative_resource_pairs"], [])
        self.assertTrue(score["event_disagreements_are_review_evidence_not_confirmed_cases"])

    def test_disagreement_rows_distinguish_false_negative_from_false_positive(self):
        source = payload([pair("A", "F", ["e1"])])
        row = build_review_queue(source)["event_reviews"][0]
        other = [["github", "issue", "unrelated-1"], ["github", "issue", "unrelated-2"]]
        score = score_review_annotations(source, [{
            "window": row["window"], "event_id": row["event_id"],
            "true_resource_pairs": [other],
        }])
        self.assertEqual((score["true_positive_pairs"], score["false_positive_pairs"],
                          score["false_negative_pairs"]), (0, 1, 1))
        self.assertEqual(len(score["event_disagreements"]), 1)
        disagreement = score["event_disagreements"][0]
        self.assertEqual(disagreement["false_positive_resource_pairs"], row["candidate_resource_pairs"])
        self.assertEqual(disagreement["false_negative_resource_pairs"], [other])
        self.assertFalse(score["complete_case_recall_measured"])
        self.assertFalse(score["held_out_verified"])

    def test_unlabeled_events_are_not_assumed_negative(self):
        data = payload([pair("A", "F", ["a", "b"])])
        row = build_review_queue(data)["event_reviews"][0]
        with self.assertRaisesRegex(ValueError, "all surfaced review events"):
            score_review_annotations(data, [{"window": row["window"],
                "event_id": row["event_id"], "true_resource_pairs": []}])

    def test_fail_closed_on_revocation_truncation_duplicate_and_unknown_event(self):
        data = payload([pair("A", "F", ["a"])])
        label = {"window": "current", "event_id": "a", "true_resource_pairs": []}
        for changed, labels in (
            ({**data, "saved_history_access": "revoked"}, [label]),
            ({**data, "coverage": {"scan_truncated": True}}, [label]),
            (data, [label, label]),
            (data, [{**label, "event_id": "unseen"}]),
        ):
            with self.assertRaises(ValueError):
                score_review_annotations(changed, labels)


class DuplicatePairProvenanceTests(unittest.TestCase):
    def test_duplicate_pair_rows_rejected_even_with_distinct_events(self):
        source = payload([pair("A", "F", ["first"]), pair("A", "F", ["second"])])
        with self.assertRaisesRegex(ValueError, "duplicate resource pair assignment"):
            build_review_queue(source)

    def test_duplicate_event_id_in_one_source_pair_is_rejected(self):
        # A repeated label is not two independent observations.
        with self.assertRaisesRegex(ValueError, "duplicate evidence event ID"):
            build_review_queue(payload([pair("A", "F", ["same", "same"])]))

    def test_duplicate_earlier_window_with_different_timezone_is_rejected(self):
        # Equivalent instants must not be represented as separate corroborations.
        earlier = [
            {"session_started_at": "2026-11-01T09:00:00Z", "evidence_event_ids": ["old1"]},
            {"session_started_at": "2026-11-01T10:00:00+01:00", "evidence_event_ids": ["old2"]},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate earlier session window"):
            build_review_queue(payload([pair("A", "F", ["new"], earlier)]))

    def test_reversed_duplicate_pair_identity_is_rejected(self):
        first = pair("A", "F", ["first"])
        reversed_pair = {**first, "resource_pointers": list(reversed(first["resource_pointers"])),
                         "current_evidence_event_ids": ["second"]}
        with self.assertRaisesRegex(ValueError, "duplicate resource pair assignment"):
            build_review_queue(payload([first, reversed_pair]))


class ReviewQueueTests(unittest.TestCase):
    def test_denied_revoked_and_unknown_access_never_generate_review_rows(self):
        for mode in ("off", "none", "denied", "revoked", "future_mode", None):
            data = payload([pair("A", "F", ["e1"])])
            data["saved_history_access"] = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                build_review_queue(data)

    def test_explicit_current_run_scope_is_review_only(self):
        data = payload([pair("A", "F", ["e1"])])
        data["saved_history_access"] = "current_run"
        out = build_review_queue(data)
        self.assertEqual(out["event_reviews_count"], 1)
        self.assertFalse(out["event_reviews"][0]["confirmed_case"])

    def test_interleaved_unrelated_events_are_never_attributed(self):
        out = build_review_queue(payload([pair("A", "F", ["a1"], [
            {"session_started_at": "2026-11-01T09:00:00Z", "evidence_event_ids": ["a0"]}])]))
        self.assertEqual([x["event_id"] for x in out["event_reviews"]], ["a0", "a1"])
        self.assertFalse(out["unlisted_events_are_unassigned"])
        self.assertTrue(all(not x["confirmed_case"] for x in out["event_reviews"]))

    def test_multi_pair_event_is_ambiguous_not_forced_to_first(self):
        out = build_review_queue(payload([pair("A", "F", ["shared"]), pair("B", "G", ["shared"])]))
        self.assertEqual(out["event_reviews_count"], 1)
        self.assertEqual(out["event_reviews"][0]["review_status"], "ambiguous_multiple_pairs")
        self.assertEqual(len(out["event_reviews"][0]["candidate_resource_pairs"]), 2)

    def test_same_id_in_different_windows_is_rejected(self):
        # A single source event cannot independently corroborate two sessions.
        with self.assertRaisesRegex(ValueError, "reused across session windows"):
            build_review_queue(payload([pair("A", "F", ["id"], [
                {"session_started_at": "2026-11-01", "evidence_event_ids": ["id"]}])]))

    def test_distinct_ids_in_different_windows_remain_separate(self):
        out = build_review_queue(payload([pair("A", "F", ["current-id"], [
            {"session_started_at": "2026-11-01", "evidence_event_ids": ["earlier-id"]}])]))
        self.assertEqual(out["event_reviews_count"], 2)
        self.assertEqual({row["event_id"] for row in out["event_reviews"]},
                         {"earlier-id", "current-id"})
        self.assertTrue(all(not row["confirmed_case"] for row in out["event_reviews"]))

    def test_equivalent_timezone_windows_cannot_hide_multi_pair_ambiguity(self):
        a = {"session_started_at": "2026-11-01T09:00:00Z",
             "evidence_event_ids": ["shared"]}
        b = {"session_started_at": "2026-11-01T10:00:00+01:00",
             "evidence_event_ids": ["shared"]}
        out = build_review_queue(payload([
            pair("A", "F", [], [a]), pair("B", "G", [], [b])]))
        self.assertEqual(out["event_reviews_count"], 1)
        self.assertEqual(out["event_reviews"][0]["review_status"],
                         "ambiguous_multiple_pairs")
        self.assertFalse(out["event_reviews"][0]["confirmed_case"])

    def test_malformed_or_naive_session_windows_fail_closed(self):
        for value in ("2026-99-99", "2026-11-01T09:00:00",
                      "2026-11-01T09:00:00Zextra", "2", 123, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "timestamp"):
                build_review_queue(payload([pair("A", "F", [], [
                    {"session_started_at": value, "evidence_event_ids": ["old"]}])]))

    def test_truncation_propagates(self):
        data = payload([pair("A", "F", ["e"], current_evidence_truncated=True)])
        self.assertTrue(build_review_queue(data)["coverage_incomplete"])

    def test_malformed_coverage_refused_instead_of_crashing(self):
        data = payload([pair("A", "F", ["e"])])
        data["coverage"] = ["invalid"]
        with self.assertRaisesRegex(ValueError, "coverage"):
            build_review_queue(data)

    def test_unbounded_session_key_cannot_enter_review_output(self):
        data = payload([pair("A", "F", ["e"], [
            {"session_started_at": "2" * 10000, "evidence_event_ids": ["old"]}])])
        with self.assertRaisesRegex(ValueError, "timestamp"):
            build_review_queue(data)

    def test_rejects_unsafe_or_unbounded_data(self):
        for change in (
            {"saved_history_access": "none"},
            {"authoritative": True},
            {"observed_pair_event_assignments": [pair("A", "F", ["e"] * 9)]},
            {"observed_pair_event_assignments": [{**pair("A", "F", ["e"]), "proves_same_case": True}]},
        ):
            data = payload([pair("A", "F", ["e"])])
            data.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_review_queue(data)


    def test_grant_scoped_resumption_exposes_review_without_cross_case_contamination(self):
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        a = "owg:r:" + "a" * 32
        b = "owg:r:" + "b" * 32
        f = "owg:f:" + "f" * 32
        def meta(ref, coobserved=False):
            data = {"resource_reference": {"resource_ref": ref, "provider": "github", "resource_kind": "issue"}}
            if coobserved:
                data["file_reference"] = {"file_ref": f}
            return json.dumps(data)
        rows = [
            ("old-pair", "2026-10-12T09:00:00Z", meta(a, True)),
            ("old-unrelated", "2026-10-12T09:01:00Z", meta(b)),
            ("new-unrelated", "2026-10-14T10:00:00Z", meta(b)),
            ("new-pair", "2026-10-14T10:01:00Z", meta(a, True)),
            ("new-single", "2026-10-14T10:02:00Z", meta(a)),
        ]
        db.executemany("INSERT INTO events VALUES (?, ?, ?)", rows)
        result = resumption_candidates(db, lease={"mode": "all_saved"})
        review = result["event_assignment_review"]
        self.assertEqual(review["event_reviews_count"], 2)
        self.assertEqual({r["event_id"] for r in review["event_reviews"]}, {"old-pair", "new-pair"})
        self.assertTrue(all(not r["confirmed_case"] for r in review["event_reviews"]))
        self.assertFalse(review["unlisted_events_are_unassigned"])
        self.assertFalse(review["coverage_incomplete"])

    def test_interleaved_two_cases_and_interruptions_keep_exact_event_provenance(self):
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        def metadata(case):
            return json.dumps({
                "resource_reference": {
                    "resource_ref": "owg:r:" + case * 32,
                    "provider": "github", "resource_kind": "issue"},
                "file_reference": {"file_ref": "owg:f:" + case * 32}})
        rows = [
            ("a-old", "2026-10-12T09:00:00Z", metadata("a")),
            ("b-old", "2026-10-12T09:01:00Z", metadata("b")),
            ("a-new", "2026-10-14T10:00:00Z", metadata("a")),
            ("b-new", "2026-10-14T10:01:00Z", metadata("b")),
            ("a-return", "2026-10-14T10:15:00Z", metadata("a")),
        ]
        db.executemany("INSERT INTO events VALUES (?, ?, ?)", rows)
        response = resumption_candidates(db, lease={"mode": "all_saved"})
        review = response["event_assignment_review"]
        by_event = {row["event_id"]: row for row in review["event_reviews"]}
        self.assertEqual(set(by_event), {row[0] for row in rows})
        self.assertTrue(all(len(row["candidate_resource_pairs"]) == 1 for row in by_event.values()))
        for event_id, row in by_event.items():
            expected = "a" if event_id.startswith("a-") else "b"
            identities = row["candidate_resource_pairs"][0]
            self.assertTrue(all(pointer[2].endswith(expected * 32) for pointer in identities))
            self.assertFalse(row["confirmed_case"])
        self.assertFalse(review["unlisted_events_are_unassigned"])
        self.assertFalse(review["coverage_incomplete"])


    def test_duplicate_event_id_and_timestamp_across_rows_cannot_forge_pair(self):
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        issue = {"resource_reference": {"resource_ref": "owg:r:" + "a" * 32,
                                         "provider": "github", "resource_kind": "issue"}}
        file_ref = {"file_reference": {"file_ref": "owg:f:" + "f" * 32}}
        both = {**issue, **file_ref}
        rows = [
            ("old-real", "2026-10-12T09:00:00Z", json.dumps(both)),
            ("new-real", "2026-10-14T10:00:00Z", json.dumps(both)),
            ("collision", "2026-10-14T10:01:00Z", json.dumps(issue)),
            ("collision", "2026-10-14T10:01:00Z", json.dumps(file_ref)),
        ]
        db.executemany("INSERT INTO events VALUES (?, ?, ?)", rows)
        review = resumption_candidates(db, lease={"mode": "all_saved"})["event_assignment_review"]
        self.assertEqual({row["event_id"] for row in review["event_reviews"]},
                         {"old-real", "new-real"})
        self.assertFalse(review["unlisted_events_are_unassigned"])


    def test_duplicate_label_on_distinct_real_pairs_is_not_a_multi_case_event(self):
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        def metadata(letter):
            return json.dumps({"resource_reference": {"resource_ref": "owg:r:" + letter * 32,
                "provider": "github", "resource_kind": "issue"},
                "file_reference": {"file_ref": "owg:f:" + letter * 32}})
        db.executemany("INSERT INTO events VALUES (?, ?, ?)", [
            ("old-a", "2026-10-12T09:00:00Z", metadata("a")),
            ("old-b", "2026-10-12T09:01:00Z", metadata("b")),
            ("collision", "2026-10-14T10:00:00Z", metadata("a")),
            ("collision", "2026-10-14T10:01:00Z", metadata("b")),
        ])
        result = resumption_candidates(db, lease={"mode": "all_saved"})
        review = result["event_assignment_review"]
        self.assertEqual(review["event_reviews_count"], 0)
        self.assertEqual(review["event_reviews"], [])
        self.assertTrue(review["coverage_incomplete"])
        self.assertEqual(result["coverage"]["ambiguous_event_id_collisions"], 1)

    def test_collided_only_pair_does_not_corroborate_continuity(self):
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        both = json.dumps({
            "resource_reference": {"resource_ref": "owg:r:" + "a" * 32,
                                   "provider": "github", "resource_kind": "issue"},
            "file_reference": {"file_ref": "owg:f:" + "f" * 32}})
        db.executemany("INSERT INTO events VALUES (?, ?, ?)", [
            ("old", "2026-10-12T09:00:00Z", both),
            ("collision", "2026-10-14T10:00:00Z", both),
            ("collision", "2026-10-14T10:01:00Z", both),
        ])
        result = resumption_candidates(db, lease={"mode": "all_saved"})
        self.assertEqual(result["observed_group_continuity"], [])
        self.assertEqual(result["observed_pair_event_assignments"], [])
        self.assertEqual(result["event_assignment_review"]["event_reviews"], [])
        self.assertTrue(result["event_assignment_review"]["coverage_incomplete"])
        self.assertFalse(any(c["case_assignment_status"] == "corroborated_candidate"
                             for c in result["candidates"]))

    def test_group_evidence_excludes_single_resource_and_transitive_only_events(self):
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        def meta(issue, file_ref=None):
            out = {"resource_reference": {"resource_ref": "owg:r:" + issue * 32,
                    "provider": "github", "resource_kind": "issue"}}
            if file_ref:
                out["file_reference"] = {"file_ref": "owg:f:" + file_ref * 32}
            return json.dumps(out)
        db.executemany("INSERT INTO events VALUES (?, ?, ?)", [
            ("pair-old", "2026-10-12T09:00:00Z", meta("a", "f")),
            ("pair-new", "2026-10-14T10:00:00Z", meta("a", "f")),
            ("issue-only", "2026-10-14T10:01:00Z", meta("a")),
        ])
        result = resumption_candidates(db, lease={"mode": "all_saved"})
        group = next(g for g in result["observed_case_groups"] if g["resource_count"] == 2)
        self.assertEqual(group["evidence_event_ids"], ["pair-new"])
        self.assertNotIn("issue-only", group["evidence_event_ids"])
        self.assertFalse(group["proves_same_case"])

    def test_authorized_empty_history_exposes_empty_review_contract(self):
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        result = resumption_candidates(db, lease={"mode": "all_saved"})
        self.assertEqual(result["event_assignment_review"]["event_reviews"], [])
        self.assertEqual(result["event_assignment_review"]["event_reviews_count"], 0)
        self.assertTrue(result["event_assignment_review"]["requires_human_review"])
        self.assertFalse(result["event_assignment_review"]["coverage_incomplete"])
        self.assertFalse(result["authoritative"])

    def test_revoked_resumption_cannot_expose_review(self):
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
        db.execute("INSERT INTO events VALUES (?, ?, ?)", (
            "sensitive", "2026-10-14T10:00:00Z", json.dumps({"resource_reference": {
                "resource_ref": "owg:r:" + "a" * 32, "provider": "github", "resource_kind": "issue"}})))
        denied = resumption_candidates(db, lease={"mode": "off"},
                                       run_started_at="2026-10-14T00:00:00Z")
        self.assertEqual(denied["observed_pair_event_assignments"], [])
        self.assertNotIn("event_assignment_review", denied)


    def test_adversarial_interleaving_permutations_do_not_leak_pair_evidence(self):
        """Synthetic invariance gate, NOT independently labeled held-out F1."""
        import itertools
        import json
        import sqlite3
        from server.work_resumption import resumption_candidates

        def metadata(letter):
            return json.dumps({
                "resource_reference": {
                    "resource_ref": "owg:r:" + letter * 32,
                    "provider": "github", "resource_kind": "issue"},
                "file_reference": {"file_ref": "owg:f:" + letter * 32}})

        # Each case has independent co-observed evidence in both windows.
        # Other cases and an unpaired interruption must not enter its pair.
        old = [(f"{c}-old", f"2026-10-12T09:0{i}:00Z", metadata(c))
               for i, c in enumerate("abc")]
        current = [(f"{c}-new", f"2026-10-14T10:0{i}:00Z", metadata(c))
                   for i, c in enumerate("abc")]
        interruption = ("interrupt", "2026-10-14T10:04:00Z",
                        json.dumps({"unrelated": "no_resource_pair"}))
        expected = {f"{c}-{window}" for c in "abc" for window in ("old", "new")}
        # Vary ingestion order without changing observed timestamps.
        for permutation in itertools.permutations(current):
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
            db.executemany("INSERT INTO events VALUES (?, ?, ?)",
                           old + list(permutation) + [interruption])
            review = resumption_candidates(db, lease={"mode": "all_saved"})[
                "event_assignment_review"]
            by_event = {row["event_id"]: row for row in review["event_reviews"]}
            self.assertEqual(set(by_event), expected)
            self.assertFalse(review["coverage_incomplete"])
            for event_id, row in by_event.items():
                letter = event_id[0]
                self.assertEqual(len(row["candidate_resource_pairs"]), 1)
                self.assertTrue(all(pointer[2].endswith(letter * 32)
                                    for pointer in row["candidate_resource_pairs"][0]))
                self.assertFalse(row["confirmed_case"])
            db.close()



class FingerprintScopeTests(unittest.TestCase):
    def test_same_rows_different_access_modes_have_different_digest(self):
        source = payload([pair("A", "F", ["e1"])])
        first = build_review_queue(source)
        changed = dict(source, saved_history_access="current_run")
        second = build_review_queue(changed)
        self.assertEqual(first["event_reviews"], second["event_reviews"])
        self.assertNotEqual(first["evidence_sha256"], second["evidence_sha256"])

    def test_truncated_evidence_changes_digest_and_cannot_be_scored(self):
        from server.reconstruction_review_cli import score_bound_review_annotations
        source = payload([pair("A", "F", ["e1"])])
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        envelope = {"evidence_sha256": queue["evidence_sha256"], "annotations": [
            {"window": row["window"], "event_id": row["event_id"],
             "true_resource_pairs": row["candidate_resource_pairs"]}
        ]}
        changed = dict(source, coverage={"scan_truncated": True})
        self.assertNotEqual(queue["evidence_sha256"], build_review_queue(changed)["evidence_sha256"])
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            score_bound_review_annotations(changed, envelope)

    def test_identity_timeline_truncation_invalidates_labels_and_scoring(self):
        from server.reconstruction_review_cli import score_bound_review_annotations, score_review_annotations
        source = payload([pair("A", "F", ["e1"])])
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"]}]
        changed = dict(source, identity_timelines_truncated=True)
        incomplete = build_review_queue(changed)
        self.assertTrue(incomplete["coverage_incomplete"])
        self.assertNotEqual(queue["evidence_sha256"], incomplete["evidence_sha256"])
        with self.assertRaisesRegex(ValueError, "incomplete source evidence"):
            score_review_annotations(changed, labels)
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            score_bound_review_annotations(changed, {
                "evidence_sha256": queue["evidence_sha256"], "annotations": labels})
        with self.assertRaisesRegex(ValueError, "invalid identity_timelines_truncated"):
            build_review_queue(dict(source, identity_timelines_truncated="false"))
        capped = dict(source, identity_timelines=[{"session": n} for n in range(13)])
        self.assertTrue(build_review_queue(capped)["coverage_incomplete"])
        self.assertEqual(build_review_queue(capped)["evidence_sha256"],
                         build_review_queue(dict(capped, identity_timelines_truncated=True))["evidence_sha256"])
        with self.assertRaisesRegex(ValueError, "incomplete source evidence"):
            score_review_annotations(capped, labels)

class LiveReconstructionServiceTests(unittest.TestCase):
    def test_live_service_rebuilds_evidence_and_refuses_stale_envelope(self):
        from unittest.mock import patch
        from server.work_resumption import score_live_reconstruction_review
        source = payload([pair("A", "F", ["e1"])])
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        envelope = {"evidence_sha256": queue["evidence_sha256"], "annotations": [
            {"window": row["window"], "event_id": row["event_id"],
             "true_resource_pairs": row["candidate_resource_pairs"]}]}
        lease = {"mode": "all_saved"}
        with patch("server.work_resumption.resumption_candidates", return_value=source) as rebuild:
            scored = score_live_reconstruction_review(
                object(), envelope, lease=lease, authorize_review=lambda: True)
            self.assertEqual(scored["true_positive_pairs"], 1)
            self.assertFalse(scored["held_out_verified"])
            self.assertEqual(rebuild.call_args.kwargs["lease"], lease)
        changed = payload([pair("A", "F", ["different-event"])])
        with patch("server.work_resumption.resumption_candidates", return_value=changed):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                score_live_reconstruction_review(
                    object(), envelope, lease=lease, authorize_review=lambda: True)

    def test_revocation_during_reconstruction_or_scoring_refuses_result(self):
        from unittest.mock import patch
        from server.work_resumption import score_live_reconstruction_review
        source = payload([pair("A", "F", ["e1"])])
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        envelope = {"evidence_sha256": queue["evidence_sha256"], "annotations": [
            {"window": row["window"], "event_id": row["event_id"],
             "true_resource_pairs": row["candidate_resource_pairs"]}]}
        checks = iter([True, False])
        with patch("server.work_resumption.resumption_candidates", return_value=source) as rebuild:
            with self.assertRaisesRegex(PermissionError, "revoked during scoring"):
                score_live_reconstruction_review(
                    object(), envelope, lease={"mode": "all_saved"},
                    authorize_review=lambda: next(checks))
            rebuild.assert_called_once()

    def test_revocation_after_scoring_does_not_leak_result(self):
        from unittest.mock import patch
        from server.work_resumption import score_live_reconstruction_review
        source = payload([pair("A", "F", ["e1"])])
        row = build_review_queue(source)["event_reviews"][0]
        envelope = {"evidence_sha256": build_review_queue(source)["evidence_sha256"],
                    "annotations": [{"window": row["window"], "event_id": row["event_id"],
                                     "true_resource_pairs": row["candidate_resource_pairs"]}]}
        decisions = iter([True, False])
        with patch("server.work_resumption.resumption_candidates", return_value=source):
            with self.assertRaises(PermissionError):
                score_live_reconstruction_review(
                    object(), envelope, lease={"mode": "all_saved"},
                    authorize_review=lambda: next(decisions))

    def test_live_service_requires_reviewable_history_scope_before_database_access(self):
        from unittest.mock import patch
        from server.work_resumption import score_live_reconstruction_review
        with patch("server.work_resumption.resumption_candidates") as rebuild:
            for lease in ({"mode": "off"}, {"mode": "unexpected"},
                          {"mode": "selected_range", "since": "bad", "until": "bad"}, {}):
                with self.subTest(lease=lease):
                    with self.assertRaisesRegex(PermissionError, "reviewable saved-history grant"):
                        score_live_reconstruction_review(
                            object(), {}, lease=lease, authorize_review=lambda: True,
                            run_started_at=None)
            rebuild.assert_not_called()

    def test_live_service_denial_precedes_database_reconstruction(self):
        from unittest.mock import patch
        from server.work_resumption import score_live_reconstruction_review
        with patch("server.work_resumption.resumption_candidates") as rebuild:
            for denied in (None, False, 1, {"authorized": True}):
                with self.assertRaises(PermissionError):
                    score_live_reconstruction_review(
                        object(), {}, lease={}, authorize_review=lambda d=denied: d)
            with self.assertRaises(PermissionError):
                score_live_reconstruction_review(
                    object(), {}, lease={}, authorize_review=None)
            rebuild.assert_not_called()


    def test_live_service_rejects_lease_scope_change_mid_scan(self):
        from unittest.mock import patch
        from server.work_resumption import score_live_reconstruction_review
        source = payload([pair("A", "F", ["e1"])])
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        envelope = {"evidence_sha256": queue["evidence_sha256"],
                    "annotations": [{"window": row["window"], "event_id": row["event_id"],
                                     "true_resource_pairs": row["candidate_resource_pairs"]}]}
        lease = {"mode": "all_saved"}
        def change_lease(*args, **kwargs):
            lease["mode"] = "off"
            return source
        with patch("server.work_resumption.resumption_candidates", side_effect=change_lease) as rebuild:
            with self.assertRaisesRegex(PermissionError, "lease changed"):
                score_live_reconstruction_review(
                    object(), envelope, lease=lease, authorize_review=lambda: True)
            self.assertEqual(rebuild.call_args.kwargs["lease"], {"mode": "all_saved"})

    def test_offline_adapter_rechecks_revocation_after_scoring(self):
        from unittest.mock import patch
        from server.work_resumption import score_authorized_reconstruction_review
        source = payload([pair("A", "F", ["e1"])])
        queue = build_review_queue(source)
        row = queue["event_reviews"][0]
        envelope = {"evidence_sha256": queue["evidence_sha256"],
                    "annotations": [{"window": row["window"], "event_id": row["event_id"],
                                     "true_resource_pairs": row["candidate_resource_pairs"]}]}
        decisions = iter([True, False])
        with self.assertRaisesRegex(PermissionError, "revoked during scoring"):
            score_authorized_reconstruction_review(
                source, envelope, authorize_review=lambda: next(decisions))

class WindowMetricsTests(unittest.TestCase):
    def test_separate_earlier_window_failure_is_not_hidden_by_current_success(self):
        source = payload([pair(
            "A", "F", ["current-1"],
            earlier=[{"session_started_at": "2026-11-01T08:00:00+00:00",
                      "evidence_event_ids": ["earlier-1"]}],
        )])
        queue = build_review_queue(source)
        annotations = []
        for row in queue["event_reviews"]:
            annotations.append({
                "window": row["window"], "event_id": row["event_id"],
                "true_resource_pairs": row["candidate_resource_pairs"]
                if row["window"] == "current" else [],
            })
        score = score_review_annotations(source, annotations)
        windows = score["per_window_metrics"]
        self.assertEqual(windows["current"]["f1"], 1.0)
        earlier = [v for k, v in windows.items() if k.startswith("earlier:")]
        self.assertEqual(len(earlier), 1)
        self.assertEqual(earlier[0]["false_positive_pairs"] if "false_positive_pairs" in earlier[0] else earlier[0]["fp"], 1)
        self.assertEqual(earlier[0]["f1"], 0.0)
        self.assertFalse(score["held_out_verified"])
        self.assertFalse(score["cross_case_contamination_measured"])



class LiveQueueAdapterTests(unittest.TestCase):
    def test_queue_uses_trusted_lease_and_matches_offline_review(self):
        from unittest.mock import patch
        from server.work_resumption import build_live_reconstruction_review_queue
        source = payload([pair("A", "F", ["e1"])])
        lease = {"mode": "all_saved"}
        with patch("server.work_resumption.resumption_candidates", return_value=source) as rebuild:
            result = build_live_reconstruction_review_queue(
                object(), lease=lease, authorize_review=lambda: True)
        self.assertEqual(result, build_review_queue(source))
        self.assertEqual(rebuild.call_args.kwargs["lease"], lease)

    def test_queue_denies_before_database_read_without_history_grant(self):
        from unittest.mock import patch
        from server.work_resumption import build_live_reconstruction_review_queue
        with patch("server.work_resumption.resumption_candidates") as rebuild:
            for lease, authorize in [({"mode": "off"}, lambda: True),
                                     ({"mode": "all_saved"}, lambda: False)]:
                with self.assertRaises(PermissionError):
                    build_live_reconstruction_review_queue(
                        object(), lease=lease, authorize_review=authorize)
            rebuild.assert_not_called()

    def test_queue_rejects_revocation_during_build(self):
        from unittest.mock import patch
        from server.work_resumption import build_live_reconstruction_review_queue
        decisions = iter([True, False])
        with patch("server.work_resumption.resumption_candidates",
                   return_value=payload([pair("A", "F", ["e1"])])):
            with self.assertRaisesRegex(PermissionError, "revoked"):
                build_live_reconstruction_review_queue(
                    object(), lease={"mode": "all_saved"},
                    authorize_review=lambda: next(decisions))

    def test_queue_rejects_mid_scan_scope_change(self):
        from unittest.mock import patch
        from server.work_resumption import build_live_reconstruction_review_queue
        lease = {"mode": "all_saved"}
        def change_scope(*args, **kwargs):
            lease["mode"] = "off"
            return payload([pair("A", "F", ["e1"])])
        with patch("server.work_resumption.resumption_candidates",
                   side_effect=change_scope):
            with self.assertRaisesRegex(PermissionError, "lease changed"):
                build_live_reconstruction_review_queue(
                    object(), lease=lease, authorize_review=lambda: True)



class CompletenessMetadataValidationTests(unittest.TestCase):
    def test_malformed_top_level_and_coverage_flags_are_rejected(self):
        for scope, field in [
            ("source", "observed_pair_event_assignments_truncated"),
            ("source", "observed_group_continuity_truncated"),
            ("source", "observed_case_groups_truncated"),
            ("coverage", "scan_truncated"),
        ]:
            for invalid in ("false", "true", 0, 1, None, []):
                source = payload([pair("A", "F", ["e1"])])
                target = source if scope == "source" else source.setdefault("coverage", {})
                target[field] = invalid
                with self.subTest(scope=scope, field=field, invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "completeness flag"):
                        build_review_queue(source)

    def test_malformed_pair_and_session_flags_are_rejected(self):
        for field in ("current_evidence_truncated", "earlier_sessions_truncated"):
            source = payload([pair("A", "F", ["e1"])])
            source["observed_pair_event_assignments"][0][field] = "false"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "completeness flag"):
                build_review_queue(source)
        source = payload([pair("A", "F", ["e1"], earlier=[{
            "session_started_at": "2026-11-01T08:00:00+00:00",
            "evidence_event_ids": ["earlier-e1"], "evidence_truncated": "false",
        }])])
        with self.assertRaisesRegex(ValueError, "completeness flag"):
            build_review_queue(source)

    def test_malformed_flags_cannot_hide_behind_true_truncation(self):
        # All metadata must be validated, even if another flag already marks
        # the scan incomplete. A short-circuiting OR used to skip these checks.
        source = payload([pair("A", "F", ["e1"])])
        source["observed_pair_event_assignments_truncated"] = True
        source["observed_group_continuity_truncated"] = "false"
        with self.assertRaisesRegex(ValueError, "completeness flag"):
            build_review_queue(source)

        source = payload([pair("A", "F", ["e1"])])
        source["observed_pair_event_assignments"][0]["current_evidence_truncated"] = True
        source["observed_pair_event_assignments"][0]["earlier_sessions_truncated"] = "false"
        with self.assertRaisesRegex(ValueError, "completeness flag"):
            build_review_queue(source)

    def test_real_boolean_truncation_refuses_scoring(self):
        source = payload([pair("A", "F", ["e1"])])
        source["coverage"]["scan_truncated"] = True
        queue = build_review_queue(source)
        self.assertTrue(queue["coverage_incomplete"])
        with self.assertRaisesRegex(ValueError, "incomplete source evidence"):
            score_review_annotations(source, [{
                "window": "current", "event_id": "e1", "true_resource_pairs": [],
            }])



class AmbiguityStratifiedScoringTests(unittest.TestCase):
    def test_shared_event_error_is_visible_separately_from_single_pair_success(self):
        source = payload([pair("A", "F", ["shared"]), pair("B", "C", ["shared", "only-b"])])
        queue = build_review_queue(source)
        annotations = []
        for row in queue["event_reviews"]:
            true_pairs = row["candidate_resource_pairs"]
            if row["event_id"] == "shared":
                # Reject the incorrect A/F link; keep the independently observed B/C link.
                true_pairs = [candidate for candidate in true_pairs
                              if {identity[2] for identity in candidate} != {"A", "F"}]
            annotations.append({"window": row["window"], "event_id": row["event_id"],
                                "true_resource_pairs": true_pairs})
        scored = score_review_annotations(source, annotations)
        cohorts = scored["per_ambiguity_metrics"]
        self.assertEqual(cohorts["ambiguous_multiple_pairs"]["events"], 1)
        self.assertEqual(cohorts["ambiguous_multiple_pairs"]["tp"], 1)
        self.assertEqual(cohorts["ambiguous_multiple_pairs"]["fp"], 1)
        self.assertEqual(cohorts["ambiguous_multiple_pairs"]["precision"], 0.5)
        self.assertEqual(cohorts["single_pair"]["events"], 1)
        self.assertEqual(cohorts["single_pair"]["fp"], 0)
        self.assertEqual(cohorts["single_pair"]["f1"], 1.0)
        self.assertFalse(scored["cross_case_contamination_measured"])
        self.assertFalse(scored["held_out_verified"])

if __name__ == "__main__":
    unittest.main()


class ProvisionalGroupAuthorityTests(unittest.TestCase):
    def test_navigation_groups_never_become_confirmed_cases(self):
        source = payload([pair("A", "F", ["e1"])])
        source["observed_case_groups"] = [{"proves_same_case": False, "resource_pointers": []}]
        self.assertTrue(build_review_queue(source)["no_case_identity_inferred"])
        for group in ({"proves_same_case": True}, {"resource_pointers": []}, None, "case"):
            with self.subTest(group=group):
                source["observed_case_groups"] = [group]
                with self.assertRaisesRegex(ValueError, "provisional group must not assert case identity"):
                    build_review_queue(source)


    def test_untrusted_group_evidence_is_bounded_before_digest(self):
        source = payload([pair("A", "F", ["e1"])])
        for group, error in (
            ({"proves_same_case": False, "description": "x" * 70000}, "unbounded provisional group evidence"),
            ({"proves_same_case": False, "description": float("nan")}, "invalid provisional group evidence"),
            ({"proves_same_case": False, "description": object()}, "invalid provisional group evidence"),
        ):
            with self.subTest(error=error):
                source["observed_case_groups"] = [group]
                with self.assertRaisesRegex(ValueError, error):
                    build_review_queue(source)

    def test_group_container_is_bounded_and_typed(self):
        source = payload([pair("A", "F", ["e1"])])
        for groups in (None, {}, [{"proves_same_case": False}] * 25):
            with self.subTest(groups=groups):
                source["observed_case_groups"] = groups
                with self.assertRaisesRegex(ValueError, "invalid or unbounded provisional case groups"):
                    build_review_queue(source)

class ReturnedEvidenceCountIntegrityTests(unittest.TestCase):
    def test_api_adapter_counts_match_bounded_visible_evidence(self):
        source = payload([pair("A", "F", ["e1"])])
        source.update({
            "identity_timelines": [{"id": "timeline"}],
            "identity_timelines_returned_count": 1,
            "observed_pair_event_assignments_returned_count": 1,
            "observed_group_continuity": [],
            "observed_group_continuity_returned_count": 0,
            "observed_case_groups": [],
            "observed_case_groups_returned_count": 0,
        })
        self.assertEqual(build_review_queue(source)["event_reviews_count"], 1)
        for field in ("identity_timelines_returned_count",
                      "observed_pair_event_assignments_returned_count",
                      "observed_group_continuity_returned_count",
                      "observed_case_groups_returned_count"):
            with self.subTest(field=field):
                tampered = dict(source, **{field: source[field] + 1})
                with self.assertRaisesRegex(ValueError, "inconsistent " + field):
                    build_review_queue(tampered)
                tampered = dict(source, **{field: True})
                with self.assertRaisesRegex(ValueError, "inconsistent " + field):
                    build_review_queue(tampered)

    def test_http_cap_count_must_not_claim_unshown_records(self):
        source = payload([pair("A", "F", ["e1"])])
        source["identity_timelines"] = [{"id": str(i)} for i in range(13)]
        source["identity_timelines_returned_count"] = 12
        self.assertTrue(build_review_queue(source)["coverage_incomplete"])
        source["identity_timelines_returned_count"] = 13
        with self.assertRaisesRegex(ValueError, "inconsistent identity_timelines_returned_count"):
            build_review_queue(source)



class IdentityTimelineResourceBoundsTests(unittest.TestCase):
    def test_oversized_hidden_timeline_refused_before_digest(self):
        source = payload([pair("A", "F", ["e1"])])
        source["identity_timelines"] = [{"id": str(i)} for i in range(12)] + [
            {"id": "hidden", "data": "x" * 65536}
        ]
        with self.assertRaisesRegex(ValueError, "unbounded identity timeline evidence"):
            build_review_queue(source)

    def test_malformed_hidden_timeline_refused(self):
        source = payload([pair("A", "F", ["e1"])])
        source["identity_timelines"] = [{"id": str(i)} for i in range(12)] + [None]
        with self.assertRaisesRegex(ValueError, "invalid identity timeline evidence"):
            build_review_queue(source)

    def test_unbounded_timeline_collection_refused(self):
        source = payload([pair("A", "F", ["e1"])])
        source["identity_timelines"] = [{"id": str(i)} for i in range(257)]
        with self.assertRaisesRegex(ValueError, "invalid or unbounded identity timelines"):
            build_review_queue(source)

    def test_bounded_over_cap_marks_incomplete(self):
        source = payload([pair("A", "F", ["e1"])])
        source["identity_timelines"] = [{"id": str(i)} for i in range(13)]
        self.assertTrue(build_review_queue(source)["coverage_incomplete"])


class MalformedAnnotationIdentityTests(unittest.TestCase):
    def test_non_scalar_annotation_identity_refused_as_validation_error(self):
        source = payload([pair("A", "F", ["e1"])])
        for field in ("window", "event_id"):
            for invalid in ([], {}, None, 1, True):
                with self.subTest(field=field, invalid=invalid):
                    annotation = {"window": "current", "event_id": "e1",
                                  "true_resource_pairs": []}
                    annotation[field] = invalid
                    with self.assertRaisesRegex(ValueError, "invalid annotated event identity"):
                        score_review_annotations(source, [annotation])

    def test_valid_annotation_identity_still_scores(self):
        source = payload([pair("A", "F", ["e1"])])
        annotation = {"window": "current", "event_id": "e1",
                      "true_resource_pairs": []}
        result = score_review_annotations(source, [annotation])
        self.assertEqual(result["false_positive_pairs"], 1)
        self.assertFalse(result["held_out_verified"])


class MalformedUnicodeEvidenceTests(unittest.TestCase):
    def test_unpaired_surrogates_are_validation_errors_not_server_crashes(self):
        # JSON transports can carry escaped lone surrogates. UTF-8 evidence
        # hashing must reject them with a handled ValueError, not UnicodeError.
        for field, value in (
            ("observed_case_groups", [{"proves_same_case": False, "label": chr(0xD800)}]),
            ("identity_timelines", [{"label": chr(0xD800)}]),
            ("observed_group_continuity", [{"label": chr(0xD800)}]),
            ("coverage", {"scan_truncated": False, "label": chr(0xD800)}),
        ):
            with self.subTest(field=field):
                source = payload([pair("A", "F", ["e1"])])
                source[field] = value
                with self.assertRaises(ValueError):
                    build_review_queue(source)


class AdversarialInterleavingIsolationTests(unittest.TestCase):
    def test_shared_resource_does_not_create_transitive_cross_case_event_links(self):
        # Two interleaved workflows mention a common file, but no source event
        # observes both outer issues. A connected-component case inference
        # would incorrectly invent a cross-case pair here.
        source = payload([
            pair("case-A", "shared", ["a-before", "a-after"]),
            pair("case-B", "shared", ["b-between"]),
        ])
        rows = build_review_queue(source)["event_reviews"]
        by_event = {row["event_id"]: row for row in rows}
        self.assertEqual(set(by_event), {"a-before", "b-between", "a-after"})
        self.assertEqual(by_event["a-before"]["candidate_resource_pairs"],
                         by_event["a-after"]["candidate_resource_pairs"])
        self.assertNotEqual(by_event["a-before"]["candidate_resource_pairs"],
                            by_event["b-between"]["candidate_resource_pairs"])
        self.assertTrue(all(len(row["candidate_resource_pairs"]) == 1 for row in rows))
        self.assertTrue(all(row["confirmed_case"] is False for row in rows))
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"]}
                  for row in rows]
        score = score_review_annotations(source, labels)
        self.assertEqual(score["false_positive_pairs"], 0)
        # This is a synthetic regression, NOT an independently labeled holdout.
        self.assertFalse(score["held_out_verified"])
        self.assertFalse(score["cross_case_contamination_measured"])


class CrossCaseContaminationScoringTests(unittest.TestCase):
    """Synthetic adversarial checks, not independent held-out measurements."""

    def test_transitive_bridge_does_not_assign_unrelated_events(self):
        source = payload([pair("case-A", "shared", ["event-A"]),
                          pair("case-C", "shared", ["event-C"])])
        queue = build_review_queue(source)
        self.assertEqual(queue["event_reviews_count"], 2)
        self.assertEqual(queue["ambiguous_event_reviews_count"], 0)
        rows = {row["event_id"]: row for row in queue["event_reviews"]}
        self.assertEqual(set(rows), {"event-A", "event-C"})
        self.assertNotEqual(rows["event-A"]["candidate_resource_pairs"],
                            rows["event-C"]["candidate_resource_pairs"])
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"]}
                  for row in queue["event_reviews"]]
        result = score_review_annotations(source, labels)
        self.assertEqual(result["false_positive_pairs"], 0)
        self.assertFalse(result["held_out_verified"])

    def test_shared_event_ambiguity_exposes_cross_case_false_positive(self):
        source = payload([pair("case-A", "shared", ["same-event"]),
                          pair("case-C", "shared", ["same-event"])])
        queue = build_review_queue(source)
        self.assertEqual(queue["event_reviews_count"], 1)
        self.assertEqual(queue["ambiguous_event_reviews_count"], 1)
        row = queue["event_reviews"][0]
        self.assertEqual(row["review_status"], "ambiguous_multiple_pairs")
        self.assertFalse(row["confirmed_case"])
        labels = [{"window": row["window"], "event_id": row["event_id"],
                   "true_resource_pairs": row["candidate_resource_pairs"][:1]}]
        result = score_review_annotations(source, labels)
        self.assertEqual(result["false_positive_pairs"], 1)
        self.assertFalse(result["held_out_verified"])


class IndependentlySpecifiedSyntheticTruthTests(unittest.TestCase):
    """Fixed synthetic truth is specified before looking at proposed pair rows.

    This is deliberately NOT a human-labeled or independently held-out cohort.
    It protects the measurement path against self-labeling and silent bridges.
    """

    def test_interleaved_interruptions_score_false_links_by_session(self):
        # The observation plan deliberately includes an interruption that
        # falsely bridges both cases, plus a legitimate later resumption.
        earlier = [{"session_started_at": "2026-11-01T09:00:00Z",
                    "evidence_event_ids": ["a-before"]}]
        source = payload([
            pair("case-A", "file-A", ["a-return", "interruption"], earlier),
            pair("case-B", "file-B", ["b-between", "interruption"]),
        ])
        # Ground truth is declared separately from the reconstruction output.
        truth = {
            ("earlier:2026-11-01T09:00:00+00:00", "a-before"): [
                [["github", "issue", "case-A"], ["local_file", "file", "file-A"]]],
            ("current", "a-return"): [
                [["github", "issue", "case-A"], ["local_file", "file", "file-A"]]],
            ("current", "b-between"): [
                [["github", "issue", "case-B"], ["local_file", "file", "file-B"]]],
            ("current", "interruption"): [],
        }
        queue = build_review_queue(source)
        self.assertEqual({(r["window"], r["event_id"]) for r in queue["event_reviews"]},
                         set(truth))
        labels = [{"window": window, "event_id": event,
                   "true_resource_pairs": pairs}
                  for (window, event), pairs in truth.items()]
        measured = score_review_annotations(source, labels)
        self.assertEqual((measured["true_positive_pairs"],
                          measured["false_positive_pairs"],
                          measured["false_negative_pairs"]), (3, 2, 0))
        self.assertAlmostEqual(measured["precision"], 3 / 5)
        self.assertAlmostEqual(measured["recall"], 1.0)
        self.assertAlmostEqual(measured["f1"], 0.75)
        self.assertEqual([r["event_id"] for r in measured["event_disagreements"]],
                         ["interruption"])
        self.assertEqual(queue["ambiguous_event_reviews_count"], 1)
        self.assertTrue(queue["no_case_identity_inferred"])
        self.assertFalse(measured["held_out_verified"])
        self.assertEqual(measured["explicit_negative_events"], 1)
        self.assertEqual(measured["explicit_negative_events_with_false_links"], 1)
        self.assertEqual(measured["explicit_negative_false_link_rate"], 1.0)
        self.assertEqual(measured["false_positive_events"], 1)
        self.assertEqual(measured["false_negative_events"], 0)
        self.assertTrue(measured["explicit_negative_metrics_are_reviewer_labels_not_held_out"])
        self.assertFalse(measured["cross_case_contamination_measured"])
        self.assertFalse(measured["real_work_accuracy_claim"])


class ExplicitNegativeReviewMetricTests(unittest.TestCase):
    def test_no_negative_labels_reports_undefined_rate_not_zero(self):
        source = payload([pair("A", "F", ["e1"])])
        labels = [{"window": "current", "event_id": "e1",
                   "true_resource_pairs": [[["github", "issue", "A"],
                                            ["local_file", "file", "F"]]]}]
        result = score_review_annotations(source, labels)
        self.assertEqual(result["explicit_negative_events"], 0)
        self.assertIsNone(result["explicit_negative_false_link_rate"])
        self.assertFalse(result["held_out_verified"])

    def test_negative_event_with_candidate_is_counted_as_false_link(self):
        source = payload([pair("A", "F", ["e1"])])
        labels = [{"window": "current", "event_id": "e1", "true_resource_pairs": []}]
        result = score_review_annotations(source, labels)
        self.assertEqual(result["explicit_negative_events"], 1)
        self.assertEqual(result["explicit_negative_events_with_false_links"], 1)
        self.assertEqual(result["explicit_negative_false_link_rate"], 1.0)
        self.assertFalse(result["missed_source_events_measured"])
        self.assertFalse(result["cross_case_contamination_measured"])


class CompleteSourceCohortTests(unittest.TestCase):
    """Synthetic adversarial tests, not an independently verified holdout."""

    def test_unlisted_positive_events_count_as_false_negatives(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([pair("A", "F", ["observed"])])
        candidate = build_review_queue(source)["event_reviews"][0]["candidate_resource_pairs"][0]
        labels = [
            {"window": "current", "event_id": "observed", "true_resource_pairs": [candidate]},
            {"window": "current", "event_id": "missed", "true_resource_pairs": [candidate]},
            {"window": "current", "event_id": "interruption", "true_resource_pairs": []},
        ]
        score = score_source_event_cohort(source, labels)
        self.assertEqual(score["true_positive_pairs"], 1)
        self.assertEqual(score["false_negative_pairs"], 1)
        self.assertEqual(score["source_events_without_surfaced_predictions"], 2)
        self.assertEqual(score["positive_source_events_without_predictions"], 1)
        self.assertEqual(score["recall"], 0.5)
        self.assertFalse(score["held_out_verified"])
        self.assertFalse(score["source_event_inventory_exhaustiveness_verified"])

    def test_cross_case_negative_link_is_counted_and_missing_inventory_rejected(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([pair("A", "F", ["cross_case"])])
        labels = [{"window": "current", "event_id": "cross_case", "true_resource_pairs": []}]
        score = score_source_event_cohort(source, labels)
        self.assertEqual(score["false_positive_pairs"], 1)
        self.assertEqual(score["negative_source_events_with_false_links"], 1)
        self.assertEqual(score["negative_false_link_rate"], 1.0)
        self.assertEqual(score["positive_source_events"], 0)
        self.assertIsNone(score["positive_false_link_rate"])
        with self.assertRaisesRegex(ValueError, "omits surfaced"):
            score_source_event_cohort(source, [
                {"window": "current", "event_id": "other", "true_resource_pairs": []}
            ])
        with self.assertRaisesRegex(ValueError, "duplicate source"):
            score_source_event_cohort(source, labels + labels)

    def test_source_cohort_disagreements_remain_event_scoped_across_interleaving(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([
            pair("case-A", "shared", ["a-before", "a-after"]),
            pair("case-B", "shared", ["b-between"]),
        ])
        rows = {r["event_id"]: r for r in build_review_queue(source)["event_reviews"]}
        labels = [
            {"window": "current", "event_id": "a-before",
             "true_resource_pairs": rows["a-before"]["candidate_resource_pairs"]},
            {"window": "current", "event_id": "b-between", "true_resource_pairs": []},
            {"window": "current", "event_id": "a-after",
             "true_resource_pairs": rows["a-after"]["candidate_resource_pairs"]},
            {"window": "current", "event_id": "missed",
             "true_resource_pairs": rows["b-between"]["candidate_resource_pairs"]},
        ]
        score = score_source_event_cohort(source, labels)
        self.assertEqual((score["true_positive_pairs"], score["false_positive_pairs"],
                          score["false_negative_pairs"]), (2, 1, 1))
        self.assertEqual(score["per_window_counts"]["current"]["events"], 4)
        self.assertEqual(score["per_window_counts"]["current"]["negative_false_link_events"], 1)
        self.assertEqual(score["source_events_with_false_links"], 1)
        self.assertEqual(score["source_event_false_link_rate"], 0.25)
        self.assertEqual(score["per_window_counts"]["current"]["false_link_events"], 1)
        # Three events have positive labels (two surfaced, one missed).
        # The only false link belongs to the explicitly negative b-between event.
        self.assertEqual(score["per_window_counts"]["current"]["positive_events"], 3)
        self.assertEqual(score["per_window_counts"]["current"]["positive_false_link_events"], 0)
        self.assertEqual(score["per_window_metrics"]["current"]["positive_false_link_rate"], 0.0)
        metrics = score["per_window_metrics"]["current"]
        self.assertAlmostEqual(metrics["precision"], 2 / 3)
        self.assertAlmostEqual(metrics["recall"], 2 / 3)
        self.assertAlmostEqual(metrics["f1"], 2 / 3)
        self.assertEqual(metrics["source_event_false_link_rate"], 0.25)
        self.assertEqual(metrics["negative_false_link_rate"], 1.0)
        disagreements = {r["event_id"]: r for r in score["event_disagreements"]}
        self.assertEqual(set(disagreements), {"b-between", "missed"})
        self.assertTrue(disagreements["b-between"]["explicit_negative_event"])
        self.assertFalse(disagreements["b-between"]["prediction_missing"])
        self.assertTrue(disagreements["missed"]["prediction_missing"])
        self.assertEqual(len(disagreements["missed"]["false_negative_resource_pairs"]), 1)
        self.assertTrue(score["event_disagreements_are_label_comparisons_not_confirmed_cases"])
        self.assertFalse(score["held_out_verified"])

    def test_false_link_on_positive_event_is_not_hidden_by_negative_only_rate(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([
            pair("case-A", "file-A", ["shared"]),
            pair("case-B", "file-B", ["shared"]),
        ])
        row = build_review_queue(source)["event_reviews"][0]
        # The event really supports case A; case B is an erroneous extra link.
        score = score_source_event_cohort(source, [
            {"window": "current", "event_id": "shared",
             "true_resource_pairs": row["candidate_resource_pairs"][:1]}
        ])
        self.assertEqual(score["negative_source_events"], 0)
        self.assertIsNone(score["negative_false_link_rate"])
        self.assertEqual(score["source_events_with_false_links"], 1)
        self.assertEqual(score["source_event_false_link_rate"], 1.0)
        self.assertEqual(score["positive_source_events"], 1)
        self.assertEqual(score["positive_source_events_with_false_links"], 1)
        self.assertEqual(score["positive_false_link_rate"], 1.0)
        self.assertIsNone(score["per_window_metrics"]["current"]["negative_false_link_rate"])
        self.assertEqual(score["per_window_metrics"]["current"]["source_event_false_link_rate"], 1.0)
        self.assertEqual(score["per_window_counts"]["current"]["false_link_events"], 1)
        self.assertEqual(score["false_positive_pairs"], 1)
        self.assertFalse(score["held_out_verified"])

    def test_cross_window_duplicate_source_id_cannot_inflate_denominator(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([pair("A", "F", ["observed"])])
        candidate = build_review_queue(source)["event_reviews"][0]["candidate_resource_pairs"][0]
        # The same source observation cannot be scored twice by placing an
        # otherwise missed event in a different session window.
        labels = [
            {"window": "current", "event_id": "observed",
             "true_resource_pairs": [candidate]},
            {"window": "earlier:2026-11-19T09:00:00+00:00", "event_id": "observed",
             "true_resource_pairs": [candidate]},
        ]
        with self.assertRaisesRegex(ValueError, "reused across windows"):
            score_source_event_cohort(source, labels)

    def test_source_inventory_rejects_fabricated_and_noncanonical_windows(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([pair("A", "F", ["observed"])])
        candidate = build_review_queue(source)["event_reviews"][0]["candidate_resource_pairs"][0]
        observed = {"window": "current", "event_id": "observed",
                    "true_resource_pairs": [candidate]}
        for invalid in ("arbitrary", "earlier:tomorrow", "earlier:2026-11-01T09:00:00Z",
                        "earlier:2026-11-01T09:00:00", "earlier:2026-11-01T10:00:00+01:00"):
            with self.subTest(window=invalid), self.assertRaisesRegex(ValueError, "noncanonical source event window"):
                score_source_event_cohort(source, [observed, {
                    "window": invalid, "event_id": "missed", "true_resource_pairs": [candidate]}])
        valid = score_source_event_cohort(source, [observed, {
            "window": "earlier:2026-11-01T09:00:00+00:00",
            "event_id": "missed", "true_resource_pairs": [candidate]}])
        self.assertEqual(valid["false_negative_pairs"], 1)
        self.assertFalse(valid["held_out_verified"])

    def test_truncated_source_cannot_be_scored(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([pair("A", "F", ["e1"])])
        source["coverage"]["scan_truncated"] = True
        with self.assertRaisesRegex(ValueError, "truncated"):
            score_source_event_cohort(source, [
                {"window": "current", "event_id": "e1", "true_resource_pairs": []}
            ])

    def test_source_cohort_stratifies_ambiguous_false_links_and_missed_events(self):
        from server.reconstruction_review_cli import score_source_event_cohort
        source = payload([
            pair("case-A", "file-A", ["ambiguous", "single"]),
            pair("case-B", "file-B", ["ambiguous"]),
        ])
        rows = {row["event_id"]: row for row in build_review_queue(source)["event_reviews"]}
        a = rows["single"]["candidate_resource_pairs"][0]
        labels = [
            {"window": "current", "event_id": "ambiguous", "true_resource_pairs": [a]},
            {"window": "current", "event_id": "single", "true_resource_pairs": [a]},
            {"window": "current", "event_id": "missing", "true_resource_pairs": [a]},
            {"window": "current", "event_id": "interruption", "true_resource_pairs": []},
        ]
        result = score_source_event_cohort(source, labels)
        strata = result["per_prediction_status_metrics"]
        self.assertEqual(strata["ambiguous_multiple_pairs"]["events"], 1)
        self.assertEqual(strata["ambiguous_multiple_pairs"]["fp"], 1)
        self.assertEqual(strata["ambiguous_multiple_pairs"]["false_link_event_rate"], 1.0)
        self.assertEqual(strata["single_pair"]["events"], 1)
        self.assertEqual(strata["single_pair"]["tp"], 1)
        self.assertEqual(strata["single_pair"]["fp"], 0)
        self.assertEqual(strata["no_prediction"]["events"], 2)
        self.assertEqual(strata["no_prediction"]["fn"], 1)
        self.assertEqual(strata["no_prediction"]["false_link_event_rate"], 0.0)
        self.assertEqual(result["true_positive_pairs"], 2)
        self.assertEqual(result["false_positive_pairs"], 1)
        self.assertEqual(result["false_negative_pairs"], 1)
        self.assertFalse(result["independent_labels_verified"])
        self.assertFalse(result["held_out_verified"])


class SourceCohortOperatorCliTests(unittest.TestCase):
    def test_source_inventory_counts_unsurfaced_events_without_claiming_holdout(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        from server.reconstruction_review_cli import main
        source = payload([pair("A", "F", ["e1"])])
        row = build_review_queue(source)["event_reviews"][0]
        inventory = {"evidence_sha256": build_review_queue(source)["evidence_sha256"], "source_events": [
            {"window": "current", "event_id": "e1", "true_resource_pairs": row["candidate_resource_pairs"]},
            {"window": "current", "event_id": "e2", "true_resource_pairs": row["candidate_resource_pairs"]},
            {"window": "current", "event_id": "e3", "true_resource_pairs": []},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            source_path, labels_path = Path(directory) / "source.json", Path(directory) / "inventory.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            labels_path.write_text(json.dumps(inventory), encoding="utf-8")
            output = StringIO()
            with patch("sys.argv", ["review", str(source_path), "--source-labels", str(labels_path)]):
                with redirect_stdout(output):
                    self.assertEqual(main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["true_positive_pairs"], 1)
            self.assertEqual(result["false_negative_pairs"], 1)
            self.assertFalse(result["held_out_verified"])
            self.assertFalse(result["source_event_inventory_exhaustiveness_verified"])

    def test_source_inventory_refuses_stale_digest(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from contextlib import redirect_stderr
        from io import StringIO
        from server.reconstruction_review_cli import main
        source = payload([pair("A", "F", ["e1"])])
        with tempfile.TemporaryDirectory() as directory:
            source_path, labels_path = Path(directory) / "source.json", Path(directory) / "inventory.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            labels_path.write_text(json.dumps({"evidence_sha256": "0" * 64, "source_events": []}), encoding="utf-8")
            with patch("sys.argv", ["review", str(source_path), "--source-labels", str(labels_path)]):
                with redirect_stderr(StringIO()) as errors:
                    self.assertEqual(main(), 2)
            self.assertIn("digest mismatch", errors.getvalue())
