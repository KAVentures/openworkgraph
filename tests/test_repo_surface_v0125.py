from __future__ import annotations

from pathlib import Path

from server import exporter


ROOT = Path(__file__).resolve().parents[1]


def test_public_documentation_is_organized_without_changing_export_sources():
    assert (ROOT / "SPEC.md").is_file()

    moved = {
        "AI_GUIDE.md": ROOT / "docs" / "ai" / "guide.md",
        "AUTOMATION_CAPABILITIES.md": ROOT / "docs" / "ai" / "automation-capabilities.md",
        "PROMPT.md": ROOT / "docs" / "ai" / "starter-prompt.md",
        "NONTECHNICAL_TESTING.md": ROOT / "docs" / "testing" / "nontechnical.md",
    }
    for old_root_name, new_path in moved.items():
        assert not (ROOT / old_root_name).exists()
        assert new_path.is_file()

    assert exporter.AI_GUIDE_PATH == ROOT / "docs" / "ai" / "guide.md"
    assert exporter.PROMPT_PATH == ROOT / "docs" / "ai" / "starter-prompt.md"
    assert "workflow-evidence layer" in exporter.AI_DATA_DICTIONARY_MD
    assert "Treat raw observations as evidence" in exporter.AI_STARTER_PROMPT_MD


def test_tester_packages_preserve_public_ai_filenames_after_source_move():
    windows = (ROOT / "scripts" / "build_windows_release.ps1").read_text(encoding="utf-8")
    macos = (ROOT / "scripts" / "build_macos_release.sh").read_text(encoding="utf-8")

    assert 'docs\\ai\\guide.md' in windows
    assert 'docs\\ai\\starter-prompt.md' in windows
    assert '(Join-Path $Package "AI_GUIDE.md")' in windows
    assert '(Join-Path $Package "PROMPT.md")' in windows

    assert 'docs/ai/guide.md" "$PKG/AI_GUIDE.md' in macos
    assert 'docs/ai/starter-prompt.md" "$PKG/PROMPT.md' in macos



def test_runtime_guidance_reads_moved_capability_brief():
    from mcp_server import automation_guidance

    assert automation_guidance.CAPABILITY_BRIEF_PATH == ROOT / "docs" / "ai" / "automation-capabilities.md"
    assert "historical replayability" in automation_guidance.AUTOMATION_CAPABILITIES_MD


def test_root_public_contract_is_small_and_navigable():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "[SPEC.md](SPEC.md)" in readme
    assert "[docs/README.md](docs/README.md)" in readme
    assert "Everything more detailed belongs here." in docs_index
    assert "SPEC.md" in agents


def test_packaging_only_integrations_live_under_integrations():
    assert not (ROOT / "mcpb").exists()
    assert not (ROOT / "plugins").exists()
    assert (ROOT / "integrations" / "mcpb" / "manifest.json").is_file()
    assert (ROOT / "integrations" / "plugins" / "openworkgraph" / ".mcp.json").is_file()

    marketplace = (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    assert "./integrations/plugins/openworkgraph" in marketplace

    build_mcpb = (ROOT / "scripts" / "build_mcpb.py").read_text(encoding="utf-8")
    assert 'SOURCE = ROOT / "integrations" / "mcpb"' in build_mcpb

    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "integrations/plugins/openworkgraph/scripts/find-and-launch.mjs" in workflow
    assert "integrations/plugins/openworkgraph/scripts/check-openworkgraph.mjs" in workflow
