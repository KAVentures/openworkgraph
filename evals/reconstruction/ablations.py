from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from .fixtures import load_cases

ABLATIONS = {
    "resource_references",
    "tab_context",
    "semantic_actions",
    "safe_labels",
    "clipboard_linkage",
}

def ablate_event(event: dict[str, Any], mode: str) -> dict[str, Any]:
    out = copy.deepcopy(event)
    meta = out.get("metadata") if isinstance(out.get("metadata"), dict) else {}
    if mode == "resource_references":
        meta.pop("resource_reference", None)
    elif mode == "tab_context":
        meta.pop("tab_context_id", None)
    elif mode == "semantic_actions":
        meta.pop("semantic_action", None)
        meta.pop("semantic_action_confidence", None)
    elif mode == "safe_labels":
        out["window_title"] = out.get("app") or "Application"
        target = meta.get("target") if isinstance(meta.get("target"), dict) else {}
        target.pop("label", None)
        page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
        page["title"] = out["window_title"]
    elif mode == "clipboard_linkage":
        meta.pop("clipboard_transfer_id", None)
        meta.pop("clipboard_source_observed", None)
    else:
        raise ValueError(f"unknown ablation {mode}")
    return out

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create blind reconstruction corpus with one signal removed")
    parser.add_argument("--mode", required=True, choices=sorted(ABLATIONS))
    parser.add_argument("--cases", help="optional exported JSONL corpus")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    cases = load_cases(Path(args.cases)) if args.cases else load_cases()
    with Path(args.output).open("w", encoding="utf-8") as fh:
        for case in cases:
            payload = {
                "case_id": case["case_id"],
                "presented_evidence": [ablate_event(event, args.mode) for event in case["presented_evidence"]],
            }
            fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {len(cases)} {args.mode} cases to {args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
