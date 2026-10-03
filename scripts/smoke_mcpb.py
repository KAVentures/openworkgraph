from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMPACT_TOOLS = {
    "get_current_work_context",
    "get_context_pulse",
    "list_history",
    "search_work",
    "get_workflow_trace",
    "get_work_profile",
    "find_repeated_workflows",
    "get_workflow_evidence",
    "get_task_context",
    "how_did_similar_runs_go",
    "get_agent_runs",
    "get_agent_handoff",
    "get_playbooks",
    "get_automation_capabilities",
    "read_evidence_file",
}


def smoke(bundle: Path) -> None:
    if not bundle.is_file():
        raise SystemExit(f"MCPB bundle not found: {bundle}")

    expected_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise SystemExit("MCPB is missing manifest.json")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        entry = str((manifest.get("server") or {}).get("entry_point") or "")
        if not entry or entry not in names:
            raise SystemExit(f"MCPB server entry point is missing: {entry!r}")
        if str(manifest.get("version") or "") != expected_version:
            raise SystemExit(
                f"MCPB version {manifest.get('version')!r} does not match VERSION {expected_version!r}"
            )
        tool_names = {
            str(item.get("name") or "")
            for item in list(manifest.get("tools") or [])
            if isinstance(item, dict) and item.get("name")
        }
        if tool_names != EXPECTED_COMPACT_TOOLS:
            missing = sorted(EXPECTED_COMPACT_TOOLS - tool_names)
            extra = sorted(tool_names - EXPECTED_COMPACT_TOOLS)
            raise SystemExit(f"Unexpected MCPB tool manifest; missing={missing}, extra={extra}")

        with tempfile.TemporaryDirectory(prefix="owg-mcpb-smoke-") as tmp:
            target = Path(tmp)
            zf.extractall(target)
            entry_path = target / entry
            subprocess.run(["node", "--check", str(entry_path)], check=True)
            launcher = entry_path.read_text(encoding="utf-8")
            for marker in ("OpenWorkGraph", "mcp_server", "launcher.py", "claude_desktop"):
                if marker not in launcher:
                    raise SystemExit(f"Packaged MCPB launcher is missing expected marker: {marker}")

    print(
        f"MCPB smoke passed: version={expected_version}, tools={len(EXPECTED_COMPACT_TOOLS)}, "
        f"entry_point={entry}"
    )


def main() -> None:
    bundle = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "OpenWorkGraph-Claude.mcpb"
    smoke(bundle.resolve())


if __name__ == "__main__":
    main()
