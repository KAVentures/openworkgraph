"""Adversarial review queue tests; synthetic fixtures are not assignment-F1 evidence."""
import unittest

from server.reconstruction_review_cli import build_review_queue


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
        self.assertTrue(out["unlisted_events_are_unassigned"])
        self.assertTrue(all(not x["confirmed_case"] for x in out["event_reviews"]))

    def test_multi_pair_event_is_ambiguous_not_forced_to_first(self):
        out = build_review_queue(payload([pair("A", "F", ["shared"]), pair("B", "G", ["shared"])]))
        self.assertEqual(out["event_reviews_count"], 1)
        self.assertEqual(out["event_reviews"][0]["review_status"], "ambiguous_multiple_pairs")
        self.assertEqual(len(out["event_reviews"][0]["candidate_resource_pairs"]), 2)

    def test_same_id_in_different_windows_is_not_a_collision(self):
        out = build_review_queue(payload([pair("A", "F", ["id"], [
            {"session_started_at": "2026-11-01", "evidence_event_ids": ["id"]}])]))
        self.assertEqual(out["event_reviews_count"], 2)
        self.assertTrue(all(x["review_status"] == "observed_pair_needs_review" for x in out["event_reviews"]))

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
        self.assertTrue(review["unlisted_events_are_unassigned"])
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
        self.assertTrue(review["unlisted_events_are_unassigned"])
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
        self.assertTrue(review["unlisted_events_are_unassigned"])


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
        self.assertEqual(review["event_reviews_count"], 2)
        self.assertEqual({row["event_id"] for row in review["event_reviews"]},
                         {"old-a", "old-b"})
        self.assertTrue(review["coverage_incomplete"])
        self.assertEqual(result["coverage"]["ambiguous_event_id_collisions"], 1)

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


if __name__ == "__main__":
    unittest.main()
