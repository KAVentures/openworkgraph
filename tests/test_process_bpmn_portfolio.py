import csv
from hashlib import sha256
import io
import json

import pytest

from shared import process_bpmn_portfolio as portfolio


NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"


def document(*processes):
    return ('<definitions xmlns="' + NS + '">' + "".join(processes)
            + "</definitions>").encode()


def process(prefix="p", label="Task", extra="", documentation=""):
    return (
        f'<process id="{prefix}" name="Private Customer Name">'
        f'<startEvent id="{prefix}s" name="Start"/>'
        f'<task id="{prefix}t" name="{label}">{documentation}</task>'
        f'<endEvent id="{prefix}e" name="End"/>'
        f'<sequenceFlow id="{prefix}f1" sourceRef="{prefix}s" targetRef="{prefix}t"/>'
        f'<sequenceFlow id="{prefix}f2" sourceRef="{prefix}t" targetRef="{prefix}e"/>'
        f'{extra}</process>'
    )


def test_clean_draft_has_exact_source_pin_and_no_authority():
    raw = document(process())
    report = portfolio.audit_documents([raw])
    assert report["assessment_complete"]
    assert report["all_selected_sources_importable_as_drafts"]
    assert not report["execution_authorized"]
    assert not report["process_models_saved"]
    file = report["files"][0]
    assert file["source_sha256"] == sha256(raw).hexdigest()
    assert file["process_count"] == 1
    row = file["processes"][0]
    assert row["status"] == "draft_review_required"
    assert row["counts"]["activities"] == 3
    assert row["counts"]["transitions"] == 2
    assert row["counts"]["business_rules"] == 0
    assert "Private Customer Name" not in json.dumps(report)
    assert "process_model" not in row


def test_mixed_batch_and_independent_process_selection_are_exact():
    valid = document(process("private-id-one"))
    mixed = document(process("a"), process("b", extra='<scriptTask id="script"/>'))
    report = portfolio.audit_documents([valid, mixed, b"<invalid", None])
    assert not report["assessment_complete"]
    assert not report["all_selected_sources_importable_as_drafts"]
    assert report["summary"]["file_status"] == {
        "assessed": 2, "not_assessed": 1, "refused": 1}
    assert report["summary"]["known_processes"] == 3
    assert report["summary"]["files_with_unknown_process_count"] == 2
    assert report["summary"]["attempted_previews"] == 3
    assert report["summary"]["process_status"] == {
        "draft_review_required": 2, "refused": 1}
    assert [r["process_ordinal"] for r in report["files"][1]["processes"]] == [1, 2]
    assert report["files"][1]["processes"][1]["reason"]
    assert report["summary"]["draft_review_counts"]["activities"] == 6
    assert "private-id-one" not in json.dumps(report)


def test_review_worklist_does_not_retain_documentation_or_source_label():
    raw = document(process(label="=PRIVATE-LABEL",
                           documentation="<documentation>SECRET BODY</documentation>"))
    report = portfolio.audit_documents([raw])
    row = report["files"][0]["processes"][0]
    assert row["status"] == "draft_with_remediation"
    assert row["counts"]["placeholder_labels"] == 1
    assert row["counts"]["documentation_notes"] == 1
    assert row["counts"]["manual_review_conditions"] >= 1
    assert report["all_selected_sources_importable_as_drafts"]
    for output in (json.dumps(report), portfolio.render_csv(report)):
        assert "SECRET BODY" not in output
        assert "PRIVATE-LABEL" not in output


def test_collaboration_blocks_multi_process_preview_without_spending_budget():
    raw = document(process("a"), process("b"),
                   '<collaboration id="collab"><participant id="pool" processRef="a"/></collaboration>')
    report = portfolio.audit_documents([raw])
    assert report["assessment_complete"]
    assert not report["all_selected_sources_importable_as_drafts"]
    assert report["summary"]["attempted_previews"] == 0
    assert report["summary"]["process_status"] == {"refused": 2}


def test_duplicate_files_keep_separate_ordinals_and_same_hash():
    raw = document(process())
    report = portfolio.audit_documents([raw, raw])
    assert [row["file_ordinal"] for row in report["files"]] == [1, 2]
    assert len({row["source_sha256"] for row in report["files"]}) == 1
    assert report["summary"]["attempted_previews"] == 2
    assert report == portfolio.audit_documents([raw, raw])


def test_whole_file_budget_never_returns_partial_process_counts(monkeypatch):
    monkeypatch.setattr(portfolio, "MAX_PROCESS_PREVIEWS", 2)
    report = portfolio.audit_documents([
        document(process("a")),
        document(process("b"), process("c")),
        document(process("d")),
    ])
    assert not report["assessment_complete"]
    assert not report["all_selected_sources_importable_as_drafts"]
    assert report["summary"]["known_processes"] == 4
    assert report["summary"]["attempted_previews"] == 2
    assert report["summary"]["process_status"]["not_assessed"] == 2
    assert report["files"][1]["processes"] == []
    assert report["files"][1]["process_count"] == 2
    assert report["files"][2]["status"] == "assessed"


def test_per_file_limit_discloses_known_but_unassessed_processes(monkeypatch):
    monkeypatch.setattr(portfolio, "MAX_PROCESSES_PER_FILE", 1)
    report = portfolio.audit_documents([document(process("a"), process("b"))])
    assert report["summary"]["process_status"] == {"not_assessed": 2}
    assert report["summary"]["attempted_previews"] == 0
    assert not report["assessment_complete"]


def test_invalid_xml_entity_and_oversized_sources_are_refused():
    report = portfolio.audit_documents([
        b"<!DOCTYPE definitions [<!ENTITY private 'PRIVATE'>]><definitions/>",
        b"X" * (portfolio.MAX_BYTES + 1),
        b"",
    ])
    assert report["assessment_complete"]
    assert not report["all_selected_sources_importable_as_drafts"]
    assert report["summary"]["file_status"] == {"refused": 3}
    assert report["files"][1]["source_sha256"] is None
    assert report["files"][0]["source_sha256"] is not None
    assert "PRIVATE" not in json.dumps(report)


def test_csv_has_document_rows_and_process_rows_without_partial_totals():
    report = portfolio.audit_documents([document(process()), b"bad", None])
    rows = list(csv.DictReader(io.StringIO(portfolio.render_csv(report))))
    assert len(rows) == 3
    assert rows[0]["process_ordinal"] == "1"
    assert rows[0]["activities"] == "3"
    assert rows[1]["process_ordinal"] == ""
    assert rows[1]["activities"] == ""
    assert rows[2]["status"] == "not_assessed"


def test_cli_paths_exit_status_and_no_path_leak(tmp_path, capsys):
    valid = tmp_path / "PRIVATE-CUSTOMER.bpmn"
    valid.write_bytes(document(process()))
    assert portfolio.main([str(valid)]) == 0
    out = capsys.readouterr().out
    assert "PRIVATE-CUSTOMER" not in out
    assert json.loads(out)["assessment_complete"]
    assert portfolio.main(["--format", "csv", str(valid), str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "not_assessed" in out
    assert str(tmp_path) not in out
    valid.write_bytes(document(process(label="123")))
    assert portfolio.main(["--fail-on-remediation", str(valid)]) == 1
    assert json.loads(capsys.readouterr().out)["all_selected_sources_importable_as_drafts"]


def test_file_limit_and_empty_input(monkeypatch):
    monkeypatch.setattr(portfolio, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="at most"):
        portfolio.audit_documents([None, None])
    with pytest.raises(ValueError, match="at least"):
        portfolio.audit_documents([])


def test_unexpected_importer_exception_is_not_reported_as_source_refusal(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("unexpected failure")
    monkeypatch.setattr(portfolio, "import_bpmn", broken)
    with pytest.raises(RuntimeError, match="unexpected failure"):
        portfolio.audit_documents([document(process())])
