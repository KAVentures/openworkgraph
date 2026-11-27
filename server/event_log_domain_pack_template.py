"""Portable domain pack setup: no log data or installation authority travels.

A template is configuration only. A matching destination label is a candidate,
not evidence of semantic identity. Destination installation always uses the
existing preflight and optimistic transaction guards.
"""
from __future__ import annotations

from typing import Any

from . import event_log_domain_packs as packs
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.domain-pack-template.v1"
FIELDS = {"format", "pack_id", "version", "pack_sha256", "mapping", "parameters",
          "template_sha256"}


def _fail(message: str):
    raise workspace.EventLogWorkspaceError(message)


def _template(pack, mapping, parameters):
    body = {"format": FORMAT, "pack_id": pack["pack_id"], "version": pack["version"],
            "pack_sha256": packs._digest(pack), "mapping": mapping, "parameters": parameters}
    return {**body, "template_sha256": packs._digest(body)}


def export_template(log_id: str, pack_id: str) -> dict[str, Any]:
    """Export the current installed setup, not its reports or source identifiers."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            record = packs._record(conn, log_id, pack_id)
            if record is None:
                _fail("Install a domain pack before exporting its setup")
            pack, parsed, params = packs._parse_install(pack_id, {
                "version": record["version"], "mapping": record["mapping"],
                "parameters": record["parameters"]})
            mapping = parsed.mapping.model_dump()
            if packs._digest(packs.resolve(pack, mapping, params)) != record["definitions_sha256"]:
                _fail("Installed definitions changed; reinstall before exporting setup")
            packs._check_names(conn, log_id, parsed.mapping)
            return _template(pack, mapping, params)
        finally:
            conn.close()


def validate_template(pack_id: str, value: Any) -> tuple[dict, dict, dict]:
    """Strictly validate a caller-supplied template against the local catalog."""
    if not isinstance(value, dict) or set(value) != FIELDS or value["format"] != FORMAT:
        _fail("Expected a portable domain pack setup file")
    if value["pack_id"] != pack_id:
        _fail("The template belongs to a different domain pack")
    if not isinstance(value["version"], str):
        _fail("Invalid template version")
    for field in ("pack_sha256", "template_sha256"):
        digest = value[field]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            _fail("Invalid portable setup digest")
    if not isinstance(value["parameters"], dict) or any(type(v) not in (int, float) for v in value["parameters"].values()):
        _fail("Template parameters must be numbers")
    if not isinstance(value["mapping"], dict) or set(value["mapping"]) != {"object_types", "activities"}:
        _fail("Invalid template mapping")
    for roles in value["mapping"].values():
        if not isinstance(roles, dict) or any(not isinstance(v, str) for v in roles.values()):
            _fail("Template labels must be strings")
    pack, parsed, params = packs._parse_install(pack_id, {
        "version": value["version"], "mapping": value["mapping"], "parameters": value["parameters"]})
    if pack["version"] != packs._pack(pack_id)["version"]:
        _fail("Template version is not the latest local version; explicitly review a new setup")
    if packs._digest(pack) != value["pack_sha256"]:
        _fail("Template catalog definition does not match the local version")
    mapping = parsed.mapping.model_dump()
    # Export normalizes defaults and numbers. Require the exact portable body,
    # including all effective parameters, rather than accepting a partial file.
    expected = _template(pack, mapping, params)
    if value != expected:
        _fail("Template configuration or digest does not match; export a fresh setup")
    return pack, mapping, params


def review_template(log_id: str, pack_id: str, value: Any) -> dict[str, Any]:
    """Review exact label candidates on a different retained log; never save."""
    pack, mapping, params = validate_template(pack_id, value)
    rows = []
    suggested = {"object_types": {}, "activities": {}}
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            for kind, table, column in (("object_types", "objects", "object_type"),
                                        ("activities", "events", "activity")):
                labels = list(mapping[kind].values())
                counts = dict(conn.execute(
                    f"SELECT {column}, count(*) FROM {table} WHERE log_id=? "
                    f"AND {column} IN (SELECT unnest(?)) GROUP BY 1",
                    [log_id, labels]).fetchall()) if labels else {}
                for role in pack[kind]:
                    key = role["role"]
                    source = mapping[kind].get(key)
                    count = counts.get(source, 0)
                    status = "not_mapped_in_template" if source is None else (
                        "exact_label_candidate" if count else "label_not_found")
                    if count:
                        suggested[kind][key] = source
                    rows.append({"kind": kind, "role": key, "required": role["required"],
                                 "template_label": source, "destination_count": count,
                                 "status": status})
        finally:
            conn.close()
    return {"format": "openworkgraph.domain-pack-template-review.v1",
            "pack_id": pack_id, "version": pack["version"],
            "template_sha256": value["template_sha256"],
            "suggested_mapping": suggested, "parameters": params, "roles": rows,
            "missing_required_roles": [r["role"] for r in rows if r["required"] and r["status"] != "exact_label_candidate"],
            "missing_optional_mapped_roles": [r["role"] for r in rows if not r["required"] and r["status"] == "label_not_found"],
            "semantic_mapping_verified": False, "installation_performed": False,
            "execution_authorized": False, "fresh_preflight_required_by_ui": True}
