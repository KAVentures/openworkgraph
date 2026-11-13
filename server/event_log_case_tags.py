"""Analyst tags on cases of a saved event log.

While investigating, analysts mark cases ("checked", "root cause: missing PO",
"exclude: test data") and come back to them. A tag is a short label with an
optional note, stored locally next to the log. Tags are the analyst's own
judgement, not facts from the source: the log's events are never changed.

Cases can be tagged by ID or by a case filter, tags can be removed for some
cases or entirely, and the ``tag`` key of the case filter selects tagged cases
in every analysis. Deleting the log deletes its tags.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-case-tags.v1"
MAX_TAGS_PER_LOG = 100
MAX_TAGGED_ROWS_PER_LOG = 200_000
MAX_CASE_IDS = 1000
MAX_CASES_BY_FILTER = 100_000
_TAG = re.compile(r"^[^\x00-\x1f]{1,60}$")
CaseID = Annotated[str, Field(min_length=1, max_length=500)]



class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddTags(_Strict):
    tag: str = Field(min_length=1, max_length=60)
    note: str = Field(default="", max_length=500)
    case_ids: list[CaseID] | None = Field(default=None, min_length=1, max_length=MAX_CASE_IDS)
    cohort: dict[str, Any] | None = None
    case_type: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _one_selector(self):
        if (self.case_ids is None) == (self.cohort is None):
            raise ValueError("give either case_ids or cohort")
        return self


class RemoveTags(_Strict):
    tag: str = Field(min_length=1, max_length=60)
    case_ids: list[CaseID] | None = Field(default=None, min_length=1, max_length=MAX_CASE_IDS)


# The case_tags table is part of the workspace schema and is erased with its log by workspace.delete_log.


def _validate(model, spec: Any):
    try:
        parsed = model.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise workspace.EventLogWorkspaceError(
            "case tags are invalid: " + (where + " " if where else "") + first["msg"].removeprefix("Value error, ")) from None
    tag = parsed.tag.strip()
    if not _TAG.fullmatch(tag):
        raise workspace.EventLogWorkspaceError("a tag is 1 to 60 characters without control characters")
    return parsed, tag


def _summary(conn, log_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("""SELECT tag, count(*), max(tagged_at), count(*) FILTER (WHERE note <> '')
        FROM case_tags WHERE log_id = ? GROUP BY tag ORDER BY tag""", [log_id]).fetchall()
    return [{"tag": t, "cases": n, "last_tagged_at": at.isoformat() + "Z", "cases_with_note": notes}
            for t, n, at, notes in rows]


def add(log_id: str, spec: Any) -> dict[str, Any]:
    parsed, tag = _validate(AddTags, spec)
    chosen = cohorts.parse_cohort(parsed.cohort) if parsed.cohort is not None else None
    note = parsed.note.strip()

    def run(conn):
        if chosen is not None:
            cohorts.restrict(conn, chosen, log_id)
            selected = [r[0] for r in conn.execute(
                f"SELECT DISTINCT case_id FROM ce ORDER BY 1 LIMIT {MAX_CASES_BY_FILTER + 1}").fetchall()]
            if len(selected) > MAX_CASES_BY_FILTER:
                raise workspace.EventLogWorkspaceError(f"the filter matches more than {MAX_CASES_BY_FILTER:,} cases")
            unknown = 0
        else:
            wanted = list(dict.fromkeys(parsed.case_ids))
            conn.execute("CREATE OR REPLACE TEMP TABLE ct_wanted (case_id VARCHAR)")
            conn.executemany("INSERT INTO ct_wanted VALUES (?)", [[c] for c in wanted])
            selected = [r[0] for r in conn.execute(
                "SELECT case_id FROM ct_wanted WHERE case_id IN (SELECT case_id FROM ce) ORDER BY 1").fetchall()]
            unknown = len(wanted) - len(selected)
        if not selected:
            raise workspace.EventLogWorkspaceError("no cases to tag: none of them is in this log")
        existing_tags = {r[0] for r in conn.execute("SELECT DISTINCT tag FROM case_tags WHERE log_id = ?", [log_id]).fetchall()}
        if tag not in existing_tags and len(existing_tags) >= MAX_TAGS_PER_LOG:
            raise workspace.EventLogWorkspaceError(f"a log keeps at most {MAX_TAGS_PER_LOG} tags; remove one first")
        conn.execute("CREATE OR REPLACE TEMP TABLE ct_selected (case_id VARCHAR)")
        conn.executemany("INSERT INTO ct_selected VALUES (?)", [[c] for c in selected])
        already = conn.execute("""SELECT count(*) FROM case_tags WHERE log_id = ? AND tag = ?
            AND case_id IN (SELECT case_id FROM ct_selected)""", [log_id, tag]).fetchone()[0]
        total = conn.execute("SELECT count(*) FROM case_tags WHERE log_id = ?", [log_id]).fetchone()[0]
        if total - already + len(selected) > MAX_TAGGED_ROWS_PER_LOG:
            raise workspace.EventLogWorkspaceError(f"a log keeps at most {MAX_TAGGED_ROWS_PER_LOG:,} case tags")
        conn.execute("BEGIN")
        try:
            conn.execute("""DELETE FROM case_tags WHERE log_id = ? AND tag = ?
                AND case_id IN (SELECT case_id FROM ct_selected)""", [log_id, tag])
            conn.execute("INSERT INTO case_tags SELECT ?, case_id, ?, ?, ? FROM ct_selected",
                         [log_id, tag, note, datetime.now(timezone.utc).replace(tzinfo=None)])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"tagged": len(selected), "already_tagged": already, "unknown_case_ids": unknown,
                "tags": _summary(conn, log_id)}

    result = workspace._query(log_id, parsed.case_type, run)
    return {"format": FORMAT, "tag": tag, "note": note, "tagged": result["tagged"],
            "already_tagged": result["already_tagged"], "unknown_case_ids": result["unknown_case_ids"],
            "case_notion": result["case_notion"], "tags": result["tags"],
            "tags_are_analyst_judgement": True}


def remove(log_id: str, spec: Any) -> dict[str, Any]:
    parsed, tag = _validate(RemoveTags, spec)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            if parsed.case_ids is None:
                removed = conn.execute("SELECT count(*) FROM case_tags WHERE log_id = ? AND tag = ?", [log_id, tag]).fetchone()[0]
                conn.execute("DELETE FROM case_tags WHERE log_id = ? AND tag = ?", [log_id, tag])
            else:
                ids = list(dict.fromkeys(parsed.case_ids))
                removed = conn.execute("SELECT count(*) FROM case_tags WHERE log_id = ? AND tag = ? AND list_contains(?, case_id)",
                                       [log_id, tag, ids]).fetchone()[0]
                conn.execute("DELETE FROM case_tags WHERE log_id = ? AND tag = ? AND list_contains(?, case_id)", [log_id, tag, ids])
            return {"format": FORMAT, "tag": tag, "removed": removed, "tags": _summary(conn, log_id)}
        finally:
            conn.close()


def listing(log_id: str, tag: str | None = None, limit: int = 200) -> dict[str, Any]:
    if tag is not None and not _TAG.fullmatch(tag.strip()):
        raise workspace.EventLogWorkspaceError("a tag is 1 to 60 characters without control characters")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise workspace.EventLogWorkspaceError("limit must be an integer from 1 to 1000")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            result: dict[str, Any] = {"format": FORMAT, "log_id": log_id, "tags": _summary(conn, log_id),
                                      "max_tags": MAX_TAGS_PER_LOG, "tags_are_analyst_judgement": True}
            if tag is not None:
                rows = conn.execute("""SELECT case_id, note, tagged_at FROM case_tags WHERE log_id = ? AND tag = ?
                    ORDER BY tagged_at DESC, case_id LIMIT ?""", [log_id, tag.strip(), limit]).fetchall()
                result["cases"] = [{"case_id": c, "note": n, "tagged_at": at.isoformat() + "Z"} for c, n, at in rows]
            return result
        finally:
            conn.close()
