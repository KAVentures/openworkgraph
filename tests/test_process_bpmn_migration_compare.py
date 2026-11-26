import csv
from hashlib import sha256
import io
import json

import pytest

from shared import process_bpmn_migration_compare as comparison
from shared import process_bpmn_portfolio as portfolio


NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"


def process(prefix="p", label="Task", unsupported=False):
    extra = '<scriptTask id="' + prefix + 'bad"/>' if unsupported else ""
    return (
        f'<process id="{prefix}" name="Private name">'
        f'<startEvent id="{prefix}s" name="Start"/>'
        f'<task id="{prefix}t" name="{label}"/>'
        f'<endEvent id="{prefix}e" name="End"/>'
        f'<sequenceFlow id="{prefix}f1" sourceRef="{prefix}s" targetRef="{prefix}t"/>'
        f'<sequenceFlow id="{prefix}f2" sourceRef="{prefix}t" targetRef="{prefix}e"/>'
        f'{extra}</process>'
    )


def doc(*processes):
    return ('<definitions xmlns="' + NS + '">' + "".join(processes)
            + "</definitions>").encode()


def test_remediation_and_refusal_transitions_are_file_level_only():
    refused = doc(process(unsupported=True))
    review = doc(process(label="123"))
    clean = doc(process())
    report = comparison.compare_documents([(refused, clean), (review, clean)])
    assert report["all_after_sources_importable_as_drafts"]
    assert report["before_assessment_complete"]
    assert not report["semantic_equivalence_verified"]
    assert not report["execution_authorized"]
    assert not report["process_models_saved"]
    first, second = report["pairs"]
    assert first["before_readiness"] == "process_refused"
    assert first["after_readiness"] == "draft_review_required"
    assert first["change"] == "readiness_category_changed"
    assert first["process_status_count_deltas"]["refused"] == -1
    assert first["known_process_population_delta"] == 0
    assert not first["process_identity_matched"]
    assert second["before_readiness"] == "extra_remediation"
    assert second["process_status_count_deltas"]["draft_with_remediation"] == -1
    assert first["before"]["source_sha256"] == sha256(refused).hexdigest()
    assert first["after"]["source_sha256"] == sha256(clean).hexdigest()
    assert "Private name" not in json.dumps(report)


def test_dropping_a_process_is_not_reported_as_remediation():
    old = doc(process("a"), process("b", unsupported=True))
    new = doc(process("a"))
    pair = comparison.compare_documents([(old, new)])["pairs"][0]
    assert pair["change"] == "process_population_changed"
    assert pair["known_process_population_delta"] == -1
    assert pair["process_status_count_deltas"]["refused"] == -1
    assert not pair["process_identity_matched"]


def test_changed_bytes_with_same_counts_are_not_semantic_equivalence():
    old, new = doc(process(label="First")), doc(process(label="Second"))
    pair = comparison.compare_documents([(old, new)])["pairs"][0]
    assert pair["change"] == "same_readiness_category"
    assert pair["same_source_bytes"] is False
    assert not pair["semantic_equivalence_verified"]
    assert all(v == 0 for v in pair["process_status_count_deltas"].values())


def test_identical_bytes_are_exactly_pinned_and_report_is_deterministic():
    raw = doc(process())
    result = comparison.compare_documents([(raw, raw), (raw, raw)])
    assert result == comparison.compare_documents([(raw, raw), (raw, raw)])
    assert all(p["same_source_bytes"] for p in result["pairs"])
    assert result["summary"]["readiness_transitions"] == [
        {"before": "draft_review_required", "after": "draft_review_required", "pairs": 2}
    ]


def test_same_source_different_aggregate_is_flagged():
    raw = doc(process())
    before = portfolio.audit_documents([raw])
    after = portfolio.audit_documents([raw])
    after["files"][0]["processes"][0]["status"] = "draft_with_remediation"
    report = comparison.compare_assessments(before, after)
    assert report["pairs"][0]["change"] == "same_source_assessment_mismatch"


def test_unassessed_and_document_refusal_preserve_unknown_counts():
    clean = doc(process())
    report = comparison.compare_documents([(None, clean), (b"bad", clean)])
    first, second = report["pairs"]
    assert first["change"] == "not_comparable"
    assert first["same_source_bytes"] is None
    assert first["process_status_count_deltas"] is None
    assert second["before_readiness"] == "document_refused"
    assert second["known_process_population_delta"] is None
    assert not report["before_assessment_complete"]
    assert report["all_after_sources_importable_as_drafts"]


def test_csv_has_blank_unknown_fields_and_no_labels():
    result = comparison.compare_documents([(None, doc(process(label="PRIVATE")))])
    rows = list(csv.DictReader(io.StringIO(comparison.render_csv(result))))
    assert len(rows) == 1
    assert rows[0]["before_sha256"] == ""
    assert rows[0]["delta_refused"] == ""
    assert rows[0]["after_draft_review_required"] == "1"
    assert "PRIVATE" not in comparison.render_csv(result)


def test_independent_budget_for_each_side(monkeypatch):
    monkeypatch.setattr(portfolio, "MAX_PROCESS_PREVIEWS", 1)
    raw = doc(process())
    result = comparison.compare_documents([(raw, raw), (raw, raw)])
    assert result["pairs"][0]["same_source_bytes"]
    assert result["pairs"][1]["change"] == "not_comparable"
    assert not result["before_assessment_complete"]
    assert not result["after_assessment_complete"]


def test_cli_gate_for_unknown_or_changed_population(tmp_path, capsys):
    old, new = tmp_path / "before.bpmn", tmp_path / "after.bpmn"
    old.write_bytes(doc(process("a"), process("b")))
    new.write_bytes(doc(process("a")))
    args = ["--pair", str(old), str(new)]
    assert comparison.main(args) == 0
    assert comparison.main(args + ["--fail-on-population-change"]) == 1
    old.write_bytes(b"invalid")
    assert comparison.main(args + ["--fail-on-population-change"]) == 1
    assert str(tmp_path) not in capsys.readouterr().out
    new.write_bytes(b"invalid")
    assert comparison.main(args) == 1


def test_cli_repeated_pairs_and_csv(tmp_path, capsys):
    path = tmp_path / "same.bpmn"
    path.write_bytes(doc(process()))
    assert comparison.main(["--format", "csv", "--pair", str(path), str(path),
                            "--pair", str(path), str(path)]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert [row["pair_ordinal"] for row in rows] == ["1", "2"]


def test_invalid_pair_counts_and_limits(monkeypatch):
    raw = doc(process())
    one = portfolio.audit_documents([raw])
    two = portfolio.audit_documents([raw, raw])
    with pytest.raises(ValueError, match="exactly one"):
        comparison.compare_assessments(one, two)
    with pytest.raises(ValueError, match="at least"):
        comparison.compare_documents([])
    monkeypatch.setattr(comparison, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="at most"):
        comparison.compare_documents([(raw, raw), (raw, raw)])
