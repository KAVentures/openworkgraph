"""Local dashboard seeded with interleaved, opted-in exact-record observations (#265 acceptance)."""
from datetime import datetime, timezone
import json

from process_logs_app import app, scratch
import server.workflow_evidence_dashboard  # install the production workflow-evidence card
from shared.lifespan import extend_lifespan
from server import db

A, B, C, D, E = ("owg:r:" + ch * 32 for ch in "abcde")


def _event(n, ref, actor, session, action, *, provider="jira", kind="issue", host="work.atlassian.net"):
    today = datetime.now(timezone.utc).date().isoformat()
    return {"event_id": f"lane-{n:02d}", "organization_id": "org-1", "actor_id": actor, "session_id": session,
            "source": "browser", "event_type": action, "observed_at": f"{today}T09:{n:02d}:00+00:00",
            "app_name": "Chrome", "window_title": "PRIVATE CUSTOMER TITLE ACME-4711",
            "metadata": {"resource_reference": {"resource_ref": ref, "provider": provider, "resource_kind": kind,
                                                "host": host, "resolver_locator": "SECRET-LOCATOR-4711"}}}


def seed():
    github = {"provider": "github", "kind": "pull_request", "host": "github.com"}
    db.insert_events([
        # Three records worked on in an interleaved order by three people.
        _event(1, A, "person-1", "s1", "browser_open"),
        _event(2, B, "person-1", "s1", "browser_open"),
        _event(3, A, "person-2", "s2", "browser_click"),
        _event(4, C, "person-3", "s3", "browser_open", **github),
        _event(5, B, "person-1", "s1", "browser_submit"),
        _event(6, A, "person-1", "s1", "browser_submit"),
        _event(7, C, "person-3", "s3", "browser_submit", **github),
        # Same opaque reference seen on two hosts: an identity conflict, so no lane at all.
        _event(8, D, "person-2", "s2", "browser_open"),
        _event(9, D, "person-2", "s2", "browser_submit", host="other.atlassian.net"),
        # A record seen once is not a repeated observation.
        _event(10, E, "person-2", "s2", "browser_open"),
    ])
    with db.connect() as conn:
        snapshot = [list(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
    (scratch / "original-events.json").write_text(json.dumps(snapshot), encoding="utf-8")


extend_lifespan(app, startup=seed)
