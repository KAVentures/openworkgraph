from __future__ import annotations

"""Pure bounded readers for observed local evidence files.

This module has no FastAPI imports and performs no authorization. Callers must
authorize the observed file reference and verify the fingerprint before exposing
the returned content.
"""

import hashlib
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 80_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".jsx", ".css", ".sql", ".log", ".ini", ".cfg", ".toml",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def truncate_text(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS], True


def _text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    values = [
        str(node.text or "")
        for node in root.iter()
        if node.tag.endswith("}t")
    ]
    return "\n".join(value for value in values if value)


def _pptx_text(path: Path) -> str:
    values: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        )
        for name in names:
            root = ET.fromstring(archive.read(name))
            slide = [
                str(node.text or "")
                for node in root.iter()
                if node.tag.endswith("}t") and str(node.text or "")
            ]
            if slide:
                values.append(f"[{name}]\n" + "\n".join(slide))
    return "\n\n".join(values)


def _xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root:
                parts = [
                    str(node.text or "")
                    for node in si.iter()
                    if node.tag.endswith("}t")
                ]
                shared.append("".join(parts))

        workbook_names: dict[str, str] = {}
        try:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            targets = {
                rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
                for rel in relationships
            }
            for node in workbook.iter():
                if not node.tag.endswith("}sheet"):
                    continue
                relationship_id = next(
                    (
                        value
                        for key, value in node.attrib.items()
                        if key.endswith("}id")
                    ),
                    "",
                )
                target = targets.get(relationship_id, "")
                if target:
                    member = (
                        target.lstrip("/")
                        if target.startswith("/")
                        else "xl/" + target.lstrip("/")
                    )
                    workbook_names[member] = node.attrib.get("name", member)
        except Exception:
            workbook_names = {}

        sheet_members = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        lines: list[str] = []
        for member in sheet_members:
            lines.append(f"[Sheet: {workbook_names.get(member, member)}]")
            root = ET.fromstring(archive.read(member))
            for cell in root.iter():
                if not cell.tag.endswith("}c"):
                    continue
                ref = cell.attrib.get("r", "")
                cell_type = cell.attrib.get("t", "")
                value_node = next(
                    (node for node in cell if node.tag.endswith("}v")),
                    None,
                )
                inline_node = next(
                    (node for node in cell.iter() if node.tag.endswith("}t")),
                    None,
                )
                raw = (
                    str(value_node.text or "")
                    if value_node is not None
                    else str(inline_node.text or "")
                    if inline_node is not None
                    else ""
                )
                if cell_type == "s" and raw.isdigit():
                    index = int(raw)
                    raw = shared[index] if 0 <= index < len(shared) else raw
                if raw:
                    lines.append(f"{ref}\t{raw}")
            lines.append("")
    return "\n".join(lines)


def extract_file_text(path: Path) -> tuple[str | None, str]:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _text_file(path), "text"
    if suffix == ".docx":
        return _docx_text(path), "docx_text"
    if suffix == ".pptx":
        return _pptx_text(path), "pptx_text"
    if suffix == ".xlsx":
        return _xlsx_text(path), "xlsx_cells"
    return None, "unsupported_binary"


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_TEXT_CHARS",
    "extract_file_text",
    "sha256_file",
    "truncate_text",
]
