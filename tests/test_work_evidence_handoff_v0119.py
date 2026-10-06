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
    from shared.discovery_package import observed_tools_inventory

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
    result = observed_tools_inventory(bundles)
    assert result["apps"] == [
        {"name": "Google Chrome", "observations": 2},
        {"name": "Microsoft Excel", "observations": 1},
    ]
    assert result["sites"] == [{"hostname": "mail.google.com", "observations": 2}]
    assert "API" in result["caveat"]


def test_xlsx_on_demand_extraction_reads_cells_without_copying_file(tmp_path):
    from server.evidence_file_reader import extract_file_text

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
    text, representation = extract_file_text(path)
    assert representation == "xlsx_cells"
    assert "[Sheet: Rates]" in text
    assert "A1\tRate" in text
    assert "B1\t4.25" in text


def test_claude_plugin_marketplace_files_are_self_contained():
    root = Path(__file__).resolve().parents[1]
    marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["source"] == "./integrations/plugins/openworkgraph"
    plugin = root / "plugins" / "openworkgraph"
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "openworkgraph"
    mcp = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    args = mcp["mcpServers"]["openworkgraph"]["args"]
    assert any("CLAUDE_PLUGIN_ROOT" in value for value in args)
    assert (plugin / "skills" / "owg" / "SKILL.md").exists()
    assert (plugin / "hooks" / "hooks.json").exists()


def test_update_version_comparison_is_numeric_not_lexicographic():
    from server.update_check import is_newer_version

    assert is_newer_version("v0.119.0", "0.118.0") is True
    assert is_newer_version("0.118.1", "0.118.0") is True
    assert is_newer_version("0.118.0", "0.118.0") is False
    assert is_newer_version("0.117.9", "0.118.0") is False
    assert is_newer_version("garbage", "0.118.0") is False


def test_discovery_build_brief_is_deterministic_and_implementation_ready():
    from shared.discovery_package import build_brief_markdown

    package = {
        "study": {
            "name": "Pricing discovery",
            "starts_at": "2026-10-01T08:00:00+00:00",
            "ends_at": "2026-10-03T08:00:00+00:00",
            "implementation_context": {
                "goal": "Build a pricing helper",
                "description": "Keep final send with the worker.",
                "source": "human_provided",
            },
            "handoff_purpose": "build_tool",
        },
        "coverage": {
            "selected_execution_count": 3,
            "workflow_bundles_included": 1,
        },
        "observed_tools": {
            "apps": [{"name": "Microsoft Excel", "observations": 3}],
            "sites": [{"hostname": "mail.google.com", "observations": 2}],
            "caveat": "Observed use does not prove API access.",
        },
        "workflow_evidence": [{
            "selector": {"selected_execution_count": 3},
            "structural_alignment": {
                "high_support_steps": [{
                    "step": "Gmail · Open message",
                    "support_runs": 3,
                    "runs_total": 3,
                    "median_zero_based_position": 0,
                }],
                "less_common_observed_steps": [{
                    "step": "Excel · Edit cell",
                    "support_runs": 1,
                    "runs_total": 3,
                    "median_zero_based_position": 1,
                }],
            },
            "timing": {
                "execution_duration_seconds": {"median": 125.0},
                "observed_surface_foreground_time": [
                    {"surface": "Gmail"},
                    {"surface": "Microsoft Excel"},
                ],
            },
            "resource_types": [{"resource_kind": "spreadsheet"}],
            "data_movement": {"clipboard_transfers": [{"support_runs": 2}]},
        }],
        "human_statements": [{
            "question": "Who approves exceptions?",
            "answer": "Pricing manager.",
        }],
        "saved_unanswered_questions": [],
        "suggested_targeted_questions": [{
            "question": "What happens during the out-of-scope gap?",
        }],
        "handoff": {
            "purpose": "build_tool",
            "recommended_next_step": "Use the package as implementation evidence.",
        },
    }

    brief = build_brief_markdown(package)
    for marker in (
        "## Workflow reconstruction",
        "Observed runs in study: 3",
        "Gmail · Open message",
        "## Open implementation questions",
        "## Implementation decision table",
        "## Permissions, dependencies, and risks",
        "Observed use never proves API access",
        "human-provided context",
    ):
        assert marker in brief
    assert "what should be automated" in brief
