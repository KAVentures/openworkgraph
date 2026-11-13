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
        self.assertEqual(score["per_window_counts"]["current"], {"tp": 0, "fp": 1, "fn": 0, "events": 1})
        self.assertEqual(score["per_window_counts"]["earlier:2026-10-12T09:00:00+00:00"],
                         {"tp": 1, "fp": 0, "fn": 0, "events": 1})
        self.assertFalse(score["cross_case_contamination_measured"])
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

if __name__ == "__main__":
    unittest.main()


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

