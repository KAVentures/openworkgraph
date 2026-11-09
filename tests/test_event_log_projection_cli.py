"""Local-only aggregate command acceptance and refusal tests."""
import json
from pathlib import Path

from server.event_log_projection_cli import aggregate_file, main


def test_cli_prints_only_numeric_aggregate(tmp_path: Path, capsys):
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps({
        "definition": {"version": 1, "expression": {"field": "duration"}},
        "numeric_fields": ["duration"],
        "rows": [{"duration": 2, "private_email": "secret@example.com"},
                 {"duration": 4, "case_id": "private-case"}],
    }))
    assert main([str(path)]) == 0
    out = capsys.readouterr()
    data = json.loads(out.out)
    assert data["rows"] == 2
    assert data["sum"] == "6"
    assert data["mean"] == "3"
    assert "secret@example.com" not in out.out
    assert "private-case" not in out.out


def test_cli_refuses_extra_fields_without_echo(tmp_path: Path, capsys):
    path = tmp_path / "private-name.json"
    path.write_text(json.dumps({
        "definition": {"version": 1, "expression": {"field": "x"}},
        "numeric_fields": ["x"], "rows": [{"x": 1}], "private": "secret@example.com",
    }))
    assert main([str(path)]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "secret" not in output.err
    assert "private-name" not in output.err


def test_cli_refuses_symlink_and_large_file(tmp_path: Path, capsys):
    original = tmp_path / "original"
    original.write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(original)
    assert main([str(link)]) == 2
    oversized = tmp_path / "large"
    oversized.write_bytes(b" " * (256 * 1024 + 1))
    assert main([str(oversized)]) == 2
    assert not capsys.readouterr().out


def test_cli_refuses_duplicate_field_and_non_numeric_private_value(tmp_path: Path):
    path = tmp_path / "cohort.json"
    request = {
        "definition": {"version": 1, "expression": {"field": "x"}},
        "numeric_fields": ["x", "x"], "rows": [{"x": 1}],
    }
    path.write_text(json.dumps(request))
    assert main([str(path)]) == 2
    request["numeric_fields"] = ["x"]
    request["rows"] = [{"x": "sensitive nonnumeric text"}]
    path.write_text(json.dumps(request))
    assert main([str(path)]) == 2


def test_aggregate_file_no_raw_rows(tmp_path: Path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps({
        "definition": {"version": 1, "expression": {"field": "x"}},
        "numeric_fields": ["x"], "rows": [{"x": 7, "private": "never returned"}],
    }))
    result = aggregate_file(path)
    assert result["sum"] == "7"
    assert "never returned" not in repr(result)
