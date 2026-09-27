from __future__ import annotations

"""Privacy-safe readable labels for structural agent tool evidence.

Only fixed public runtime vocabulary and simple MCP identifiers remain readable.
Custom tool names are hashed because runtimes may embed customer names, paths,
IDs, or instructions in otherwise syntactically valid labels.
"""

import hashlib
import re
from typing import Any

from mcp_server.security import _looks_instruction_like

from .contextual_redaction import FIRST_NAMES, SURNAMES


_KNOWN_TOOL_NAMES = {name.casefold(): name for name in (
    # Claude Code
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead",
    "Bash", "BashOutput", "KillShell", "KillBash", "Grep", "Glob", "LS",
    "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "TodoRead",
    "ExitPlanMode", "Skill", "SlashCommand", "AskUserQuestion",
    # Codex CLI
    "shell", "local_shell", "exec_command", "apply_patch", "read_file",
    "write_file", "list_dir", "update_plan", "view_image", "web_search",
    "write_stdin",
    # OpenAI Agents SDK hosted tools
    "file_search", "code_interpreter", "computer_use_preview",
    "image_generation", "web_search_preview", "local_shell_call",
)}
_MCP_TOOL_RE = re.compile(
    r"^mcp__([a-z0-9][a-z0-9_-]{0,39})__([a-z0-9][a-z0-9_-]{0,59})$",
    re.I,
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_URL_RE = re.compile(r"(?:https?://|www\.)", re.I)
_LONG_ID_RE = re.compile(r"(?:\b\d{7,}\b|\b[0-9a-f]{16,}\b)", re.I)

_FIRST = frozenset(name.casefold() for name in FIRST_NAMES)
_LAST = frozenset(name.casefold() for name in SURNAMES)


def _hash(value: Any) -> str:
    raw = str(value or "")
    return "tool:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _contains_person_name(identifier: str) -> bool:
    parts = [part for part in re.split(r"[_-]+", str(identifier or "").casefold()) if part]
    return any(left in _FIRST and right in _LAST for left, right in zip(parts, parts[1:]))


def readable_tool_name(value: Any) -> str:
    """Return a readable public tool label or a stable opaque hash."""
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    low = raw.casefold()
    instruction_probe = re.sub(r"[_:.-]+", " ", raw)
    unsafe = (
        not raw
        or len(raw) > 120
        or bool(_EMAIL_RE.search(raw))
        or bool(_URL_RE.search(raw))
        or bool(_LONG_ID_RE.search(raw))
        or "/" in raw
        or "\\" in raw
        or _looks_instruction_like(instruction_probe)
    )
    if unsafe:
        return _hash(raw or "unknown")

    known = _KNOWN_TOOL_NAMES.get(low)
    if known:
        return known

    mcp = _MCP_TOOL_RE.fullmatch(raw)
    if mcp:
        server_name, tool_name = mcp.group(1).casefold(), mcp.group(2).casefold()
        if _contains_person_name(server_name):
            return _hash(raw)
        if _contains_person_name(tool_name):
            return f"mcp__{server_name}__{_hash(tool_name)}"
        return f"mcp__{server_name}__{tool_name}"

    return _hash(raw or "unknown")


def readable_structural_step(*, operation: str, status: str, tool_name: Any, tool_category: Any) -> str:
    """Readable structural step with no tool arguments/results/content."""
    op = str(operation or "").strip().casefold()
    state = str(status or "unknown").strip().casefold()
    if op == "model_call":
        return "model_call"
    if op == "tool_call":
        category = re.sub(r"[^a-z0-9_-]+", "", str(tool_category or "other").casefold()) or "other"
        label = readable_tool_name(tool_name)
        suffix = f":{state}" if state in {"error", "failed", "denied", "cancelled", "timeout"} else ""
        return f"tool:{category}:{label}{suffix}"
    if op == "handoff":
        return "handoff"
    if op == "human_approval_requested":
        return "approval_request"
    if op == "human_approval_received":
        decision = state if state in {"success", "denied", "cancelled"} else "observed"
        return f"approval_received:{decision}"
    if op == "error":
        failure = state if state in {"error", "failed", "denied", "cancelled", "timeout"} else "error"
        return f"error:{failure}"
    return ""


__all__ = ["readable_tool_name", "readable_structural_step"]
