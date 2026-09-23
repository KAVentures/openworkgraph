from __future__ import annotations

import threading
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from collector.outbox import EventOutbox
from connector.runtime import start_sync_worker, status as worker_status, stop_sync_worker
from connector.state import SyncState
from shared.evidence_deletion import add_tombstone, audit_deletion, normalize_range
from . import analytics
from .db import DATA_DIR
from .evidence_delete import delete_database_range, remove_local_screenshots, rewrite_jsonl_range
from .main import CONFIG_PATH
from .secure_app import app

_DELETE_LOCK = threading.RLock()


class EvidenceDeleteRequest(BaseModel):
    since: str
    until: str


@app.post("/v1/evidence/delete")
def delete_local_evidence(request: EvidenceDeleteRequest) -> dict[str, Any]:
    """Delete a local evidence time range without weakening existing auth.

    secure_app's capability/session middleware protects this route exactly like
    the other local dashboard mutation endpoints. The Gateway worker is stopped
    during deletion so an unsent row cannot race the local skip-range update.
    Already synchronized Gateway copies are intentionally not claimed as recalled.
    """
    try:
        since, until = normalize_range(request.since, request.until)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _DELETE_LOCK:
        was_running = bool(worker_status().get("worker_running"))
        if was_running:
            stop_sync_worker()

        # Fail closed first. Any late collector/browser delivery for this range is
        # rejected even if a later filesystem cleanup encounters an error.
        add_tombstone(since, until)

        result: dict[str, Any] | None = None
        pruned_outbox = 0
        skipped_gateway = 0
        screenshots_removed = 0
        jsonl_removed = 0
        gateway_cursor = 0
        try:
            sync_state = SyncState(DATA_DIR / "gateway_sync_state.db")
            gateway_cursor = sync_state.get_int("last_local_event_id", 0)

            # Collector JSONL and pending delivery are auxiliary local evidence
            # copies, so delete them as part of the same user operation.
            jsonl_removed = rewrite_jsonl_range(since, until)
            outbox = EventOutbox(DATA_DIR / "collector_outbox.db")
            pruned_outbox = outbox.prune_range(since, until)

            result = delete_database_range(since, until)
            local_ids = [int(value) for value in result.get("local_ids") or []]
            skipped_gateway = sync_state.add_skip_ids(local_ids, "local_evidence_deleted")
            screenshots_removed = remove_local_screenshots(list(result.get("screenshot_paths") or []))

            # The summary cache historically assumes append-only local evidence.
            # Deletion must invalidate it explicitly or the dashboard can display
            # rows that no longer exist in SQLite.
            analytics.clear_summary_cache()

            audit_deletion(
                since=since,
                until=until,
                deleted_events=int(result.get("deleted_events") or 0),
                pruned_outbox_events=pruned_outbox,
                skipped_gateway_rows=skipped_gateway,
                screenshots_removed=screenshots_removed,
            )
        except Exception as exc:
            analytics.clear_summary_cache()
            raise HTTPException(
                status_code=500,
                detail=(
                    "Local deletion was only partially completed. The requested time range remains "
                    "tombstoned so late evidence cannot repopulate it. Retry the deletion. "
                    f"Details: {str(exc)[:220]}"
                ),
            ) from exc
        finally:
            if was_running:
                start_sync_worker(CONFIG_PATH)

        local_ids = [int(value) for value in (result or {}).get("local_ids") or []]
        at_or_before_cursor = sum(1 for value in local_ids if value <= gateway_cursor)
        return {
            "status": "deleted",
            "since": since,
            "until": until,
            "deleted_events": int((result or {}).get("deleted_events") or 0),
            "jsonl_events_removed": int(jsonl_removed),
            "collector_outbox_events_removed": int(pruned_outbox),
            "gateway_local_rows_marked_never_share": int(skipped_gateway),
            "screenshots_removed": int(screenshots_removed),
            "late_delivery_suppressed": True,
            "gateway_recall_performed": False,
            "rows_at_or_before_gateway_cursor": int(at_or_before_cursor),
            "notice": (
                "This deletes the selected evidence from this computer and prevents unsent/late local copies "
                "from being shared later. Evidence already synchronized to an organization Gateway is not "
                "automatically recalled."
            ),
        }
