from __future__ import annotations

import json
import zipfile
from pathlib import Path


def test_discovery_implementation_and_step_edits_clear_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import (
        approve_share,
        finish_session,
        set_excluded_event_ids,
        set_handoff_purpose,
        set_implementation_context,
        start_session,
    )

    start_session(
        name="Build study",
        purpose="Observe pricing work",
        implementation_goal="Build a pricing helper",
        implementation_description="Use the exact rate sheet and CRM record.",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
    )
    finish_session()
    assert approve_share()["share_approved_at"]

    state = set_implementation_context(
        goal="Build a better pricing helper",
        description="Keep the worker in control of final send.",
    )
    assert state["implementation_context"]["source"] == "human_provided"
    assert state["share_approved_at"] is None

    approve_share()
    state = set_excluded_event_ids(["evt_unrelated"])
    assert state["excluded_event_ids"] == ["evt_unrelated"]
    assert state["share_approved_at"] is None

    state = set_handoff_purpose("build_tool")
    assert state["handoff_purpose"] == "build_tool"


def test_observed_tools_inventory_is_deterministic_and_has_api_caveat():
    from server.discovery_routes import _observed_tools_inventory

    bundles = [{
        "canonical_evidence": [{
            "events": [
                {
                    "app": "Microsoft Excel",
                    "metadata": {},
                },
                {
                    "app": "Google Chrome",
                    "metadata": {"page": {"hostname": "mail.google.com"}},
                },
                {
                    "app": "Google Chrome",
                    "metadata": {"page": {"hostname": "mail.google.com"}},
                },
            ],
        }],
    }]
    result = _observed_tools_inventory(bundles)
    assert result["apps"] == [
        {"name": "Google Chrome", "observations": 2},
        {"name": "Microsoft Excel", "observations": 1},
    ]
    assert result["sites"] == [{"hostname": "mail.google.com", "observations": 2}]
    assert "API" in result["caveat"]


def test_xlsx_on_demand_extraction_reads_cells_without_copying_file(tmp_path):
    from server.evidence_paging import _extract

    path = tmp_path / "Rates.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                <sheets><sheet name="Rates" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                <Relationship Id="rId1" Target="worksheets/sheet1.xml"
                 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
               </Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Rate</t></is></c>
                <c r="B1"><v>4.25</v></c></row></sheetData></worksheet>""",
        )
    text, representation = _extract(path)
    assert representation == "xlsx_cells"
    assert "[Sheet: Rates]" in text
    assert "A1\tRate" in text
    assert "B1\t4.25" in text


def test_claude_plugin_marketplace_files_are_self_contained():
    root = Path(__file__).resolve().parents[1]
    marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["source"] == "./plugins/openworkgraph"
    plugin = root / "plugins" / "openworkgraph"
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "openworkgraph"
    mcp = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    args = mcp["mcpServers"]["openworkgraph"]["args"]
    assert any("CLAUDE_PLUGIN_ROOT" in value for value in args)
    assert (plugin / "skills" / "owg" / "SKILL.md").exists()
    assert (plugin / "hooks" / "hooks.json").exists()
