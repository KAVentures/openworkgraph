from __future__ import annotations


def test_url_reference_parser_keeps_real_id_only_in_local_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    from resource_references import resource_reference_from_url
    from server.local_reference_lookup import resolve_resource_reference

    ref = resource_reference_from_url(
        "https://github.com/KAVentures/openworkgraph/pull/123?token=SECRET#frag",
        include_locator=False,
        remember_locator=True,
    )
    assert ref
    assert ref["provider"] == "github"
    assert ref["resource_kind"] == "pull_request"
    assert ref["host"] == "github.com"
    assert ref["resource_ref"].startswith("owg:r:")
    assert "resolver_locator" not in ref
    resolved = resolve_resource_reference(ref["resource_ref"])
    assert resolved["resolver_locator"] == "KAVentures/openworkgraph/pull/123"
    blob = (tmp_path / ".local_reference_lookup.json").read_text(encoding="utf-8")
    assert "SECRET" not in blob
    assert "#frag" not in blob


def test_file_reference_has_path_hash_and_no_contents(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    from collector.business_context import _file_reference
    from server.local_reference_lookup import resolve_file_reference

    document = tmp_path / "one" / "Bank_A_Rates_Oct.xlsx"
    document.parent.mkdir()
    document.write_bytes(b"rate-sheet-test")
    ref = _file_reference(str(document))
    assert ref
    assert ref["path"] == str(document.resolve())
    assert len(ref["sha256"]) == 64
    assert ref["contents_stored"] is False
    stored = resolve_file_reference(ref["file_ref"])
    assert stored["path"] == str(document.resolve())
    assert stored["sha256"] == ref["sha256"]


def test_same_filename_in_two_folders_produces_distinct_file_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    from collector.business_context import _file_reference

    refs = []
    for folder in ("a", "b"):
        document = tmp_path / folder / "Rates.xlsx"
        document.parent.mkdir()
        document.write_bytes(b"same bytes")
        refs.append(_file_reference(str(document)))
    assert refs[0]["path"] != refs[1]["path"]
    assert refs[0]["file_ref"] != refs[1]["file_ref"]


def test_google_and_salesforce_ids_parse_without_query_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    from resource_references import resource_reference_from_url
    from server.local_reference_lookup import resolve_resource_reference

    urls = [
        (
            "https://docs.google.com/spreadsheets/d/1ABCdefghijkLMNOP/edit?gid=123",
            "spreadsheet:1ABCdefghijkLMNOP",
        ),
        (
            "https://acme.my.salesforce.com/lightning/r/Opportunity/006ABCDEF123456/view?x=secret",
            "Opportunity:006ABCDEF123456",
        ),
    ]
    for url, locator in urls:
        ref = resource_reference_from_url(url, remember_locator=True)
        assert ref and "resolver_locator" not in ref
        assert resolve_resource_reference(ref["resource_ref"])["resolver_locator"] == locator


def test_compact_trace_row_exposes_hostname():
    from mcp_server.compact import _slim_trace_row

    row = _slim_trace_row({
        "event_id": "e1",
        "observed_at": "2026-10-03T10:00:00+00:00",
        "app": "Google Chrome",
        "event_type": "focus_span",
        "page_host": "example.com",
    })
    assert row["page_host"] == "example.com"


def test_safari_native_url_capture_fails_closed_for_private_or_unknown(monkeypatch):
    from collector import business_context

    monkeypatch.setattr(
        business_context,
        "_osascript",
        lambda *_args, **_kwargs: "__PRIVATE__",
    )
    assert business_context._mac_browser_url("Safari", "Example") == ""

    monkeypatch.setattr(
        business_context,
        "_osascript",
        lambda *_args, **_kwargs: "__UNKNOWN__",
    )
    assert business_context._mac_browser_url("Safari", "Example") == ""

    monkeypatch.setattr(
        business_context,
        "_osascript",
        lambda *_args, **_kwargs: "__NORMAL__\nhttps://example.com/work",
    )
    assert business_context._mac_browser_url("Safari", "Example") == "https://example.com/work"


def test_browser_reference_extension_explicitly_skips_incognito_tabs():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (
        root / "browser_extension" / "resource_reference_enrichment.js"
    ).read_text(encoding="utf-8")
    assert "if (currentTab?.incognito) return;" in source


def test_local_reference_lookup_can_forget_resolvers(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))

    from resource_references import resource_reference_from_url
    from server.local_reference_lookup import (
        forget_references,
        resolve_resource_reference,
    )

    ref = resource_reference_from_url(
        "https://github.com/KAVentures/openworkgraph/pull/321",
        remember_locator=True,
    )
    assert ref
    token = ref["resource_ref"]
    assert resolve_resource_reference(token)
    assert forget_references({token}) == 1
    assert resolve_resource_reference(token) is None
