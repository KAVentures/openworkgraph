#!/usr/bin/env python3
"""Optional CLI adapter for an *already installed* Codex or Claude Code model.

Reads the blinded test prompt from stdin. Runs the user's logged-in existing AI
CLI with no approval bypass and outputs only the final outcome JSON line.
Tool permissions/network access are governed by that CLI's own settings.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

_ALLOWED = {"resolved", "unresolved", "unknown", "paid", "unpaid", "refunded", "denied"}


def invoke(provider: str, prompt: str, *, runner=subprocess.run) -> str:
    executable = shutil.which("codex" if provider == "codex" else "claude")
    if not executable:
        raise RuntimeError(f"{provider} CLI is not installed or is not on PATH")
    if provider == "codex":
        # Codex exec accepts '-' for reading the prompt from stdin. Its
        # permissions/sandbox remain controlled by the user's configuration.
        command = [executable, "exec", "--skip-git-repo-check", "--ephemeral", "-"]
    else:
        # Claude Code -p reads piped input in noninteractive mode.
        command = [executable, "-p", "--output-format", "text", "--max-turns", "12"]
    result = runner(command, input=prompt, text=True, capture_output=True, timeout=210)
    if result.returncode != 0:
        # Do not relay arbitrary logs, which can contain private evidence.
        raise RuntimeError(f"{provider} CLI exited without a final decision")
    lines = result.stdout.strip().splitlines()
    if not lines or len(result.stdout) > 40000:
        raise RuntimeError("model CLI returned no bounded final answer")
    try:
        answer = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("model must end with a plain JSON final line") from exc
    if not isinstance(answer, dict) or answer.get("predicted_outcome") not in _ALLOWED:
        raise RuntimeError("model did not provide a recognized outcome")
    # Strip any model-provided tool logs or purported evidence receipts.
    return json.dumps({"predicted_outcome": answer["predicted_outcome"]})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["codex", "claude"])
    args = parser.parse_args()
    prompt = sys.stdin.read(12000)
    if len(prompt) >= 12000:
        raise ValueError("benchmark prompt exceeds maximum length")
    print(invoke(args.provider, prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
