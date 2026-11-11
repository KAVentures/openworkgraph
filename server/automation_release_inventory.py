"""Review-only local release inventory for customer-run automation deployments (#370 R18).

This is an operator declaration of intent/state, not a remote deployment control:
OWG does not activate, pause, roll back, verify an executable artifact, or replay
a business write. Every revision pins the metadata observed in the local run
store at the instant the human confirmed it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from . import automation_monitoring as monitoring

MAX_REVISIONS = 30
STAGES = ("planned", "shadow_reported", "pilot_reported", "production_reported",
          "paused_reported", "retired_reported")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS deployment_release_reviews (
 deployment_id VARCHAR NOT NULL, revision INTEGER NOT NULL,
 stage VARCHAR NOT NULL, release_label VARCHAR NOT NULL,
 artifact_sha256 VARCHAR, reviewer_note VARCHAR NOT NULL,
 reported_spec_sha256 VARCHAR, design_sha256 VARCHAR,
 declared_at TIMESTAMP NOT NULL,
 PRIMARY KEY (deployment_id, revision)
)
"""
DISCLAIMERS = {
    "remote_deployment_state_verified": False,
    "executable_artifact_verified": False,
    "business_outcome_verified": False,
    "remote_business_actions_triggered": False,
    "rollback_or_remote_pause_performed": False,
    "scope": "local_human_declaration_only",
}


class ReleaseReviewError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: Annotated[StrictInt, Field(ge=0, le=MAX_REVISIONS)]
    expected_reported_spec_sha256: str | None
    expected_design_sha256: str | None
    stage: Literal["planned", "shadow_reported", "pilot_reported",
                   "production_reported", "paused_reported", "retired_reported"]
    release_label: Annotated[str, Field(min_length=1, max_length=64)]
    artifact_sha256: str | None = None
    reviewer_note: Annotated[str, Field(max_length=500)] = ""
    user_confirmed: StrictBool = False

    def validate_declaration(self):
        if not _VERSION.fullmatch(self.release_label):
            raise ReleaseReviewError("release_label must be 1 to 64 printable ASCII letters, numbers, dot, dash or underscore")
        if self.artifact_sha256 is not None and not _SHA256.fullmatch(self.artifact_sha256):
            raise ReleaseReviewError("artifact_sha256 must be a lowercase SHA-256 digest")
        for name in ("expected_reported_spec_sha256", "expected_design_sha256"):
            value = getattr(self, name)
            if value is not None and not _SHA256.fullmatch(value):
                raise ReleaseReviewError(name + " must be a lowercase SHA-256 digest")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.reviewer_note):
            raise ReleaseReviewError("reviewer_note must not contain control characters")
        if not self.user_confirmed:
            raise ReleaseReviewError("Explicit review confirmation is required", 403)


def initialize(conn):
    conn.execute(SCHEMA)


def forget(conn, deployment_id: str):
    """Called inside the monitoring deletion transaction, so no stale review remains."""
    initialize(conn)
    conn.execute("DELETE FROM deployment_release_reviews WHERE deployment_id=?", [deployment_id])


def _history(conn, deployment_id: str):
    rows = conn.execute("""SELECT revision,stage,release_label,artifact_sha256,reviewer_note,
                           reported_spec_sha256,design_sha256,declared_at
                           FROM deployment_release_reviews WHERE deployment_id=?
                           ORDER BY revision DESC LIMIT ?""", [deployment_id, MAX_REVISIONS]).fetchall()
    return [
        {"revision": row[0], "stage": row[1], "release_label": row[2],
         "artifact_sha256": row[3], "reviewer_note": row[4],
         "reported_spec_sha256": row[5], "design_sha256": row[6],
         "declared_at": row[7].isoformat() + "Z"}
        for row in rows
    ]


def _view(conn, deployment_id):
    info = monitoring._check(conn, deployment_id)
    initialize(conn)
    history = _history(conn, deployment_id)
    reported = conn.execute("""SELECT count(*),
                              count(*) FILTER (WHERE reported_spec_sha256 IS NOT NULL),
                              count(*) FILTER (WHERE reported_spec_sha256=?)
                              FROM runs WHERE deployment_id=?""",
                            [info["reported_spec_sha256"], deployment_id]).fetchone()
    return {
        "format": "openworkgraph.deployment-release-inventory.v1",
        "deployment_id": deployment_id,
        "deployment_name": info["name"],
        "runtime": info["runtime"],
        "expected_reported_spec_sha256": info["reported_spec_sha256"],
        "expected_design_sha256": info["design_sha256"],
        "revision": history[0]["revision"] if history else 0,
        "current": history[0] if history else None,
        "history": history,
        "capacity": MAX_REVISIONS,
        "runs": {"retained": int(reported[0]), "declaring_any_spec": int(reported[1]),
                 "declaring_pinned_spec": int(reported[2])},
        **DISCLAIMERS,
        "interpretation": ("Operator-entered release inventory. A reported spec hash, an attached design, "
                           "or a declared production stage does not prove which artifact ran or "
                           "that any remote change occurred."),
    }


def detail(deployment_id: str):
    with monitoring._LOCK:
        conn = monitoring._connect()
        try:
            return _view(conn, deployment_id)
        finally:
            conn.close()


def record(deployment_id: str, payload):
    try:
        review = Review.model_validate(payload)
    except ValidationError:
        raise ReleaseReviewError("Invalid or missing release review fields") from None
    review.validate_declaration()
    with monitoring._LOCK:
        conn = monitoring._connect()
        try:
            conn.execute("BEGIN")
            try:
                previous = _view(conn, deployment_id)
                if previous["revision"] != review.expected_revision:
                    raise ReleaseReviewError("Release inventory changed; reload before saving", 409)
                if (previous["expected_reported_spec_sha256"], previous["expected_design_sha256"]) != (
                    review.expected_reported_spec_sha256, review.expected_design_sha256
                ):
                    raise ReleaseReviewError("Reported workflow specification or reviewed design changed; reload", 409)
                if previous["revision"] >= MAX_REVISIONS:
                    raise ReleaseReviewError("Release review history is full; nothing was pruned", 409)
                if previous["current"] and previous["current"]["stage"] == "retired_reported" and review.stage != "retired_reported":
                    raise ReleaseReviewError("A retired deployment cannot be reactivated by an inventory declaration; import as a new deployment", 409)
                if review.stage in ("pilot_reported", "production_reported"):
                    if not review.expected_reported_spec_sha256 or not review.expected_design_sha256:
                        raise ReleaseReviewError("Pilot/production declarations require both a reported spec and attached reviewed design")
                    if review.expected_reported_spec_sha256 != review.expected_design_sha256:
                        raise ReleaseReviewError("Reported spec does not match the attached reviewed design")
                    if not review.artifact_sha256:
                        raise ReleaseReviewError("Pilot/production declarations require an explicitly entered artifact SHA-256")
                    if not previous["runs"]["declaring_pinned_spec"]:
                        raise ReleaseReviewError("No retained run reports claim the pinned workflow specification")
                at = datetime.now(timezone.utc).replace(tzinfo=None)
                conn.execute("""INSERT INTO deployment_release_reviews
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [deployment_id, review.expected_revision + 1, review.stage,
                     review.release_label, review.artifact_sha256, review.reviewer_note,
                     review.expected_reported_spec_sha256, review.expected_design_sha256, at])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return _view(conn, deployment_id)
        finally:
            conn.close()


__all__ = ["Review", "ReleaseReviewError", "detail", "record", "initialize", "forget"]
