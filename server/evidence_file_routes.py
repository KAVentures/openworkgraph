from __future__ import annotations

"""Strict, non-persistent reads of files that OpenWorkGraph actually observed."""

import hashlib
import json
from pathlib import Path
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import HTTPException, Request

from shared.discovery_scope import read_state as read_discovery_state
from . import ai_context
from .db import connect
from .local_reference_lookup import resolve_file_reference
from .secure_app import app

_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_TEXT_CHARS = 80_000
_TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".jsx", ".css", ".sql", ".log", ".ini", ".cfg", ".toml",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discovery_observed(file_ref: str) -> bool:
    state = read_discovery_state()
    if not state.get("enabled") or not state.get("share_approved_at"):
        return False
    start = str(state.get("starts_at") or "")
    end = str(state.get("ends_at") or "")
    if not start or not end:
        return False
    pattern = f'%{file_ref}%'
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM events
                WHERE observed_at >= ? AND observed_at < ?
                  AND metadata_json LIKE ?
                LIMIT 1
                """,
                (start, end, pattern),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _authorized(request: Request, file_ref: str) -> tuple[bool, str]:
    if str(request.headers.get("X-OpenWorkGraph-Context") or "").strip().lower() != "ai":
        return False, "AI context request required"
    detail = ai_context.effective_detail()
    if detail.get("detail_level") == ai_context.DETAIL_FULL:
        return True, "full_ai_context"
    if _discovery_observed(file_ref):
        return True, "approved_discovery"
    return False, (
        "File reads require Full AI context or an approved Discovery study that "
        "actually observed this file."
    )


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_TEXT_CHARS:
        return text, False
    return text[:_MAX_TEXT_CHARS], True


def _text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    values = [str(node.text or "") for node in root.iter() if node.tag.endswith("}t")]
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
            wb = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {
                rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
                for rel in rels
            }
            for node in wb.iter():
                if not node.tag.endswith("}sheet"):
                    continue
                rid = next(
                    (value for key, value in node.attrib.items() if key.endswith("}id")),
                    "",
                )
                target = targets.get(rid, "")
                if target:
                    if target.startswith("/"):
                        member = target.lstrip("/")
                    else:
                        member = "xl/" + target.lstrip("/")
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


def _extract(path: Path) -> tuple[str | None, str]:
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return _text_file(path), "text"
    if suffix == ".docx":
        return _docx_text(path), "docx_text"
    if suffix == ".pptx":
        return _pptx_text(path), "pptx_text"
    if suffix == ".xlsx":
        return _xlsx_text(path), "xlsx_cells"
    return None, "unsupported_binary"


@app.post("/v1/evidence-files/read")
async def read_evidence_file(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON request") from exc
    file_ref = str((body or {}).get("file_ref") or "").strip()
    if not file_ref.startswith("owg:f:"):
        raise HTTPException(status_code=422, detail="file_ref must be an observed OpenWorkGraph file reference")

    allowed, basis = _authorized(request, file_ref)
    if not allowed:
        raise HTTPException(status_code=403, detail=basis)

    observed = resolve_file_reference(file_ref)
    if not observed:
        raise HTTPException(status_code=404, detail="observed file reference is no longer available locally")

    path = Path(str(observed.get("path") or "")).expanduser()
    try:
        stat = path.stat()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="the observed file no longer exists") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="the observed path is no longer a file")
    if stat.st_size > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="file is larger than the on-demand read limit")

    expected = str(observed.get("sha256") or "").lower()
    actual = _sha256(path)
    if not expected or actual != expected:
        raise HTTPException(
            status_code=409,
            detail="file changed since it was observed; OpenWorkGraph refused to read a different version",
        )

    text, representation = _extract(path)
    if text is None:
        return {
            "file_ref": file_ref,
            "path": str(path),
            "sha256": actual,
            "size": int(stat.st_size),
            "authorization_basis": basis,
            "representation": representation,
            "content": None,
            "contents_stored_by_openworkgraph": False,
            "instruction": "Open the file with an authorized local tool that supports this format.",
        }

    content, truncated = _truncate(text)
    return {
        "file_ref": file_ref,
        "path": str(path),
        "sha256": actual,
        "size": int(stat.st_size),
        "authorization_basis": basis,
        "representation": representation,
        "content": content,
        "truncated": truncated,
        "contents_stored_by_openworkgraph": False,
    }


__all__ = ["read_evidence_file"]
