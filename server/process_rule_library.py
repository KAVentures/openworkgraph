"""Reusable, versioned business-rule library for the local process catalog (#370 R21).

A rule set is a named group of reviewed rules (text plus an optional structured
predicate) with a source basis. Every save creates a new revision; earlier revisions
stay readable, so a design can pin the exact rule it used. Linking copies the rule's
text and predicate into the design and records a ``library_ref`` (set, revision, rule,
content digest). The drift report then lists saved designs that use an outdated
revision, or whose linked rule text was edited away from the library's.

Linking never confirms anything: the copied rule is unconfirmed until the design's
owner confirms it, and nothing here evaluates or executes a rule.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.process_model import BusinessRule, ProcessModel

from .db import DATA_DIR

FORMAT = "openworkgraph.process-rule-library.v1"
MAX_SETS = 100
MAX_REVISIONS = 200
_LOCK = threading.RLock()


class RuleLibraryError(ValueError):
    """Safe error; never echoes rule content."""


class RuleSetWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_set_id: str | None = Field(default=None, pattern=r"^rs_[0-9a-f]{16}$")
    expected_revision: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=120)
    source_label: str = Field(default="", max_length=120)
    rules: list[BusinessRule] = Field(min_length=1, max_length=40)


def _path():
    return DATA_DIR / "process_rule_library.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "sets": {}}
    return data if isinstance(data, dict) and isinstance(data.get("sets"), dict) else {"version": 1, "sets": {}}


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".owg-rules-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, separators=(",", ":"))
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def rule_digest(rule: dict[str, Any]) -> str:
    """Content identity of a library rule: its text and predicate, not its confirmation."""
    content = {"text": rule["text"], "predicate": rule.get("predicate")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save(payload: Any) -> dict[str, Any]:
    try:
        write = RuleSetWrite.model_validate(payload)
    except ValidationError:
        raise RuleLibraryError("A rule set needs a name and 1–40 valid rules") from None
    if len({r.id for r in write.rules}) != len(write.rules):
        raise RuleLibraryError("Rule IDs in a set must be unique")
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        data = _read()
        if write.rule_set_id is None:
            if write.expected_revision != 0:
                raise RuleLibraryError("A new rule set starts at revision 0")
            if len(data["sets"]) >= MAX_SETS:
                raise RuleLibraryError("The rule library is full")
            set_id = "rs_" + secrets.token_hex(8)
            history: list[dict[str, Any]] = []
        else:
            set_id = write.rule_set_id
            history = data["sets"].get(set_id)
            if history is None:
                raise RuleLibraryError("Unknown rule set")
            if history[-1]["revision"] != write.expected_revision:
                raise RuleLibraryError("This rule set changed since you opened it; reload before saving")
            if len(history) >= MAX_REVISIONS:
                raise RuleLibraryError("This rule set has reached its revision limit")
        rules = [r.model_dump() for r in write.rules]
        revision = {"rule_set_id": set_id, "revision": len(history) + 1, "name": write.name.strip(),
                    "source_label": write.source_label.strip(), "saved_at": now,
                    "rules": [{**r, "content_sha256": rule_digest(r)} for r in rules]}
        data["sets"][set_id] = history + [revision]
        _write(data)
    return revision


def get(rule_set_id: str, revision: int | None = None) -> dict[str, Any]:
    with _LOCK:
        history = _read()["sets"].get(str(rule_set_id))
    if not history:
        raise RuleLibraryError("Unknown rule set")
    if revision is None:
        return history[-1]
    match = next((r for r in history if r["revision"] == revision), None)
    if match is None:
        raise RuleLibraryError("Unknown rule set revision")
    return match


def listing() -> dict[str, Any]:
    with _LOCK:
        sets = _read()["sets"]
    return {"format": FORMAT, "rule_sets": [
        {"rule_set_id": k, "name": v[-1]["name"], "revision": v[-1]["revision"], "saved_at": v[-1]["saved_at"],
         "rule_count": len(v[-1]["rules"]), "source_label": v[-1]["source_label"]} for k, v in sets.items()],
        "evaluates_rules": False}


def link(model_value: dict[str, Any], rule_id: str, rule_set_id: str, library_rule_id: str,
         revision: int | None = None) -> dict[str, Any]:
    """Proposed model where one design rule uses a library rule; never saved here."""
    library = get(rule_set_id, revision)
    source = next((r for r in library["rules"] if r["id"] == library_rule_id), None)
    if source is None:
        raise RuleLibraryError("Unknown library rule")
    model = ProcessModel.model_validate(model_value).model_dump()
    rules = model["business_rules"]
    target = next((r for r in rules if r["id"] == rule_id), None)
    if target is None:
        raise RuleLibraryError("Unknown design rule")
    target.update(text=source["text"], user_confirmed=False,
                  library_ref={"rule_set_id": rule_set_id, "revision": library["revision"],
                               "rule_id": library_rule_id, "content_sha256": source["content_sha256"]})
    if source.get("predicate") is not None:
        target["predicate"] = source["predicate"]
    else:
        target.pop("predicate", None)
    try:
        return ProcessModel.model_validate(model).model_dump()
    except ValidationError:
        raise RuleLibraryError("The library rule does not fit this design (for example its decision table)") from None


def _find_rule(revision, rule_id):
    return next((r for r in revision["rules"] if r["id"] == rule_id), None) if revision else None


def _linked_state(sets, rule):
    ref = rule["library_ref"]
    history = sets.get(ref["rule_set_id"])
    if not history:
        return "rule_set_missing", None, None
    pinned = next((r for r in history if r["revision"] == ref["revision"]), None)
    original = _find_rule(pinned, ref["rule_id"])
    latest = history[-1]
    current = _find_rule(latest, ref["rule_id"])
    if pinned is None or original is None:
        return "pinned_rule_missing", latest, current
    if (original["content_sha256"] != ref["content_sha256"]
            or rule_digest(original) != ref["content_sha256"]):
        return "pinned_digest_mismatch", latest, current
    if rule_digest(rule) != ref["content_sha256"]:
        return "edited_in_design", latest, current
    if current is None:
        return "removed_from_library", latest, current
    if rule_digest(current) != current["content_sha256"]:
        return "latest_digest_mismatch", latest, current
    if current["content_sha256"] != ref["content_sha256"]:
        return "library_rule_changed", latest, current
    if latest["revision"] != ref["revision"]:
        return "newer_revision_same_content", latest, current
    return "current", latest, current


def _table_impact(model, rule_ids):
    direct = {t["id"] for t in model.get("decision_tables", [])
              if any(row["rule_id"] in rule_ids for row in t["rows"])}
    affected = set(direct)
    while True:
        dependents = {t["id"] for t in model.get("decision_tables", [])
                      if any(r["table_id"] in affected for r in t.get("required_decisions", []))}
        if dependents <= affected:
            break
        affected.update(dependents)
    return direct, affected


def _relink_rule(rule, revision, source):
    rule.update(text=source["text"], user_confirmed=False,
                library_ref={"rule_set_id": revision["rule_set_id"],
                             "revision": revision["revision"], "rule_id": source["id"],
                             "content_sha256": source["content_sha256"]})
    if source.get("predicate") is None:
        rule.pop("predicate", None)
    else:
        rule["predicate"] = source["predicate"]


def upgrade_review(record, selections=None):
    """Review linked-rule changes and optionally construct one unsaved model.

    Selection requires the exact previewed library revision and digest for
    every rule, and the caller separately pins the saved process. All selected
    updates succeed together or no model is proposed.
    """
    model = ProcessModel.model_validate(record["process_model"]).model_dump()
    with _LOCK:
        sets = _read()["sets"]
    rows = []
    candidates = {}
    for rule in model["business_rules"]:
        ref = rule.get("library_ref")
        if not ref:
            continue
        state, latest, current = _linked_state(sets, rule)
        direct, affected = _table_impact(model, {rule["id"]})
        row = {"rule_id": rule["id"], "rule_set_id": ref["rule_set_id"],
               "library_rule_id": ref["rule_id"], "linked_revision": ref["revision"],
               "linked_content_sha256": ref["content_sha256"], "status": state,
               "latest_revision": latest["revision"] if latest else None,
               "latest_content_sha256": current["content_sha256"] if current else None,
               "design_confirmed": rule["user_confirmed"],
               "direct_decision_table_ids": sorted(direct),
               "affected_decision_table_ids": sorted(affected),
               "text_changed": current is not None and rule["text"] != current["text"],
               "predicate_changed": current is not None and rule.get("predicate") != current.get("predicate"),
               "current_text": rule["text"], "latest_text": current["text"] if current else None,
               "current_predicate": rule.get("predicate"),
               "latest_predicate": current.get("predicate") if current else None,
               "selectable": state in {"library_rule_changed", "newer_revision_same_content"}}
        rows.append(row)
        if row["selectable"]:
            candidates[rule["id"]] = (row, rule, latest, current)
    result = {"format": "openworkgraph.rule-library-upgrade-review.v1",
              "based_on": {k: record[k] for k in ("workflow_id", "revision", "content_sha256")},
              "linked_rules": rows, "selectable_rule_count": len(candidates),
              "selected_rule_ids": [], "cleared_rule_confirmations": [],
              "affected_decision_table_ids": [], "cleared_decision_table_confirmations": [],
              "proposed_process_model": None, "process_model_saved": False,
              "evaluates_rules": False, "execution_authorized": False}
    if selections is None:
        return result
    if not isinstance(selections, list) or not 1 <= len(selections) <= 40:
        raise RuleLibraryError("Select 1–40 linked rules from the current preview")
    ids = set()
    chosen = []
    for selection in selections:
        if (not isinstance(selection, dict)
                or set(selection) != {"rule_id", "library_revision", "content_sha256"}
                or not isinstance(selection["rule_id"], str)
                or type(selection["library_revision"]) is not int
                or not isinstance(selection["content_sha256"], str)):
            raise RuleLibraryError("Select exact rule revisions and content digests")
        rule_id = selection["rule_id"]
        if rule_id in ids or rule_id not in candidates:
            raise RuleLibraryError("A selected rule is duplicated or no longer eligible; reload the preview")
        row, rule, latest, current = candidates[rule_id]
        if (selection["library_revision"] != row["latest_revision"]
                or selection["content_sha256"] != row["latest_content_sha256"]):
            raise RuleLibraryError("A selected library revision changed; reload the preview")
        ids.add(rule_id)
        chosen.append((rule, latest, current))
    direct, affected = _table_impact(model, ids)
    result["selected_rule_ids"] = sorted(ids)
    result["cleared_rule_confirmations"] = sorted(rule["id"] for rule, _, _ in chosen if rule["user_confirmed"])
    for rule, latest, current in chosen:
        _relink_rule(rule, latest, current)
    result["affected_decision_table_ids"] = sorted(affected)
    for table in model.get("decision_tables", []):
        if table["id"] in affected:
            if table["user_confirmed"]:
                result["cleared_decision_table_confirmations"].append(table["id"])
            table["user_confirmed"] = False
    result["cleared_decision_table_confirmations"].sort()
    try:
        result["proposed_process_model"] = ProcessModel.model_validate(model).model_dump()
    except ValidationError:
        raise RuleLibraryError("Selected rules do not fit the current process model; review its decision tables") from None
    return result


def drift(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Saved designs whose linked rules are behind the library or edited away from it."""
    with _LOCK:
        sets = _read()["sets"]
    findings = []
    for record in records:
        for rule in (record.get("process_model") or {}).get("business_rules", []):
            ref = rule.get("library_ref")
            if not ref:
                continue
            state, latest, _ = _linked_state(sets, rule)
            if state not in {"current", "newer_revision_same_content"}:
                base = {"workflow_id": record["workflow_id"], "revision": record["revision"],
                        "rule_id": rule["id"], "rule_set_id": ref["rule_set_id"],
                        "linked_revision": ref["revision"]}
                if latest:
                    base["latest_revision"] = latest["revision"]
                findings.append({**base, "status": state})
    return {"format": "openworkgraph.rule-library-drift.v1", "findings": findings,
            "designs_checked": len(records), "evaluates_rules": False}


__all__ = ["RuleLibraryError", "drift", "get", "link", "listing", "rule_digest", "save", "upgrade_review"]
