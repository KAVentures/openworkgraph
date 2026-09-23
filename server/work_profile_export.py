from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
WORKSHEET_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
WORKSHEET_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"

ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", DOC_REL_NS)
ET.register_namespace("", CT_NS)


def _flatten(prefix: str, value: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), child, out)
        return
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            out.append({"metric": prefix, "value": json.dumps(value, ensure_ascii=False)})
        return
    out.append({"metric": prefix, "value": value})


def profile_tables(profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    summary: list[dict[str, Any]] = []
    for key in ("fragmentation", "communication_actions", "privacy", "interpretation"):
        _flatten(key, profile.get(key) or {}, summary)
    summary.extend([
        {"metric": "scope", "value": profile.get("scope")},
        {"metric": "since", "value": profile.get("since")},
        {"metric": "manual_transfer_count", "value": profile.get("manual_transfer_count", 0)},
        {"metric": "cross_surface_manual_transfer_count", "value": profile.get("cross_surface_manual_transfer_count", 0)},
    ])
    return {
        "Work profile": summary,
        "Manual transfers": list(profile.get("manual_transfer_patterns") or []),
        "AI tool usage": list(profile.get("ai_tool_usage") or []),
        "Daily rhythm": list((profile.get("daily_rhythm") or {}).get("days") or []),
        "Hunting candidates": list(profile.get("navigation_hunting_candidates") or []),
        "Self tags": list(profile.get("self_tags") or []),
    }


def _safe(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value and value[0] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def _column_name(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, rem = divmod(value - 1, 26)
        result = chr(65 + rem) + result
    return result


def _sheet_xml(records: list[dict[str, Any]]) -> bytes:
    root = ET.Element(f"{{{MAIN_NS}}}worksheet")
    sheet_data = ET.SubElement(root, f"{{{MAIN_NS}}}sheetData")
    keys: list[str] = []
    for record in records:
        for key in record.keys():
            if key not in keys:
                keys.append(key)
    if not keys:
        keys = ["status"]
        records = [{"status": "No data captured for this table."}]
    rows = [dict(zip(keys, keys))] + records
    for row_number, record in enumerate(rows, start=1):
        row_el = ET.SubElement(sheet_data, f"{{{MAIN_NS}}}row", {"r": str(row_number)})
        for col, key in enumerate(keys):
            value = _safe(record.get(key, ""))
            ref = f"{_column_name(col)}{row_number}"
            if isinstance(value, bool):
                cell = ET.SubElement(row_el, f"{{{MAIN_NS}}}c", {"r": ref, "t": "b"})
                ET.SubElement(cell, f"{{{MAIN_NS}}}v").text = "1" if value else "0"
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                cell = ET.SubElement(row_el, f"{{{MAIN_NS}}}c", {"r": ref})
                ET.SubElement(cell, f"{{{MAIN_NS}}}v").text = str(value)
            else:
                cell = ET.SubElement(row_el, f"{{{MAIN_NS}}}c", {"r": ref, "t": "inlineStr"})
                inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
                text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
                text.text = str(value if value is not None else "")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def append_profile_sheets(xlsx_bytes: bytes, profile: dict[str, Any]) -> bytes:
    """Append derived work-profile worksheets without changing existing workbook sheets."""
    source = io.BytesIO(xlsx_bytes)
    with zipfile.ZipFile(source, "r") as zin:
        files = {name: zin.read(name) for name in zin.namelist()}

    workbook = ET.fromstring(files["xl/workbook.xml"])
    rels = ET.fromstring(files["xl/_rels/workbook.xml.rels"])
    content_types = ET.fromstring(files["[Content_Types].xml"])
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        raise ValueError("workbook has no sheets collection")

    max_sheet_id = max([int(sheet.attrib.get("sheetId", "0")) for sheet in list(sheets)] or [0])
    relation_numbers = []
    for rel in list(rels):
        match = re.fullmatch(r"rId(\d+)", str(rel.attrib.get("Id") or ""))
        if match:
            relation_numbers.append(int(match.group(1)))
    next_rel = max(relation_numbers or [0]) + 1
    sheet_numbers = []
    for name in files:
        match = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", name)
        if match:
            sheet_numbers.append(int(match.group(1)))
    next_sheet_file = max(sheet_numbers or [0]) + 1

    for table_name, records in profile_tables(profile).items():
        safe_name = table_name[:31]
        max_sheet_id += 1
        rel_id = f"rId{next_rel}"
        next_rel += 1
        target = f"worksheets/sheet{next_sheet_file}.xml"
        part_name = f"/xl/{target}"
        files[f"xl/{target}"] = _sheet_xml(records)
        next_sheet_file += 1
        ET.SubElement(sheets, f"{{{MAIN_NS}}}sheet", {
            "name": safe_name,
            "sheetId": str(max_sheet_id),
            f"{{{DOC_REL_NS}}}id": rel_id,
        })
        ET.SubElement(rels, f"{{{PKG_REL_NS}}}Relationship", {
            "Id": rel_id,
            "Type": WORKSHEET_REL_TYPE,
            "Target": target,
        })
        ET.SubElement(content_types, f"{{{CT_NS}}}Override", {
            "PartName": part_name,
            "ContentType": WORKSHEET_CONTENT_TYPE,
        })

    files["xl/workbook.xml"] = ET.tostring(workbook, encoding="utf-8", xml_declaration=True)
    files["xl/_rels/workbook.xml.rels"] = ET.tostring(rels, encoding="utf-8", xml_declaration=True)
    files["[Content_Types].xml"] = ET.tostring(content_types, encoding="utf-8", xml_declaration=True)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            zout.writestr(name, data)
    return out.getvalue()
