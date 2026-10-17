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


def test_macos_browser_detail_uses_extension_without_native_queries(monkeypatch):
    from collector import business_context
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("browser context attempted a process or native lookup")
    monkeypatch.setattr(business_context.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(business_context, "_mac_ax_document", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    business_context._CACHE.clear()
    for app in ("Safari", "Google Chrome", "Firefox"):
        for title in ("Invoice Acme Q4", "Private Browsing", "Incognito"):
            assert business_context.capture_business_context(app, title) == {}


def test_native_document_file_uri_is_local_and_portable(tmp_path):
    from collector.business_context import _file_path_from_document

    # pathlib.as_uri() uses /C:/... on Windows and /... on macOS/Linux.
    # The Accessibility mock runs on the CI host OS, not just macOS.
    document = tmp_path / "Invoice Q4 #1.xlsx"
    document.write_bytes(b"synthetic work document")
    assert _file_path_from_document(document.as_uri()) == str(document)
    assert _file_path_from_document(str(document)) == str(document)
    assert _file_path_from_document("file://remote-host/shared/document.xlsx") == ""
    assert _file_path_from_document(document.as_uri() + "?auth=private") == ""
    assert _file_path_from_document(document.as_uri() + "#fragment") == ""


def test_macos_native_document_reference_needs_only_accessibility(tmp_path, monkeypatch):
    import sys
    import types
    import subprocess
    from collector import business_context

    document = tmp_path / "Invoice Acme Q4.xlsx"
    document.write_bytes(b"synthetic work document")
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path/'data'))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path/'auth'))
    calls = []
    def attribute(obj, key, unused):
        calls.append(key)
        return (0, 'focused-window') if key == 'AXFocusedWindow' else (0, document.as_uri())
    monkeypatch.setitem(sys.modules, 'AppKit', types.SimpleNamespace(
        NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: types.SimpleNamespace(
            frontmostApplication=lambda: types.SimpleNamespace(processIdentifier=lambda: 42)))))
    ax = types.SimpleNamespace(AXIsProcessTrusted=lambda: True,
                              AXUIElementCreateApplication=lambda pid: ('app',pid),
                              AXUIElementCopyAttributeValue=attribute)
    monkeypatch.setitem(sys.modules, 'ApplicationServices', ax)
    monkeypatch.setattr(business_context.platform, 'system', lambda: 'Darwin')
    def forbidden(*args, **kwargs):
        raise AssertionError('native document lookup spawned a child process')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    business_context._CACHE.clear()
    captured=business_context.capture_business_context('Microsoft Excel','Invoice Acme Q4')
    assert captured['file_reference']['path'] == str(document.resolve())
    assert captured['file_reference']['contents_stored'] is False
    assert calls == ['AXFocusedWindow', 'AXDocument']
    ax.AXIsProcessTrusted=lambda: False
    calls.clear(); business_context._CACHE.clear()
    assert business_context.capture_business_context('Microsoft Excel','Invoice Acme Q4') == {}
    assert not calls
    # macOS Outlook selection is deliberately unsupported without Automation;
    # ordinary title capture remains in collector.platform.
    business_context._CACHE.clear()
    assert business_context.capture_business_context('Microsoft Outlook','Inbox') == {}


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
