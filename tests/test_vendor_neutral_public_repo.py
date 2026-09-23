from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".txt", ".yml", ".yaml", ".json", ".py", ".js", ".sh", ".ps1", ".bat", ".command"}
EXCLUDED_DIRS = {".git", ".venv", ".runtime", "dist", "data", "__pycache__", ".pytest_cache"}


def test_public_repo_examples_do_not_name_private_integration_targets():
    # Construct names so this guard does not contain its own forbidden literals.
    forbidden = [("A" + "kai").lower(), ("Co" + "dos").lower()]
    hits: list[str] = []

    for path in ROOT.rglob("*"):
        if not path.is_file() or path == Path(__file__):
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        for term in forbidden:
            if term in text:
                hits.append(f"{path.relative_to(ROOT)} contains a vendor-specific example name")

    assert not hits, "\n".join(hits)
