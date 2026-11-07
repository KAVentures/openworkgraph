"""Performance budgets for opt-in high-fidelity capture (#634).

Seeds an isolated local history of ordinary structural events plus hover, drag and
sampled-pointer events (40 points per batch), then times what high-fidelity capture adds:
ingest, the coverage manifest, friction candidates, the local heatmap, the retention
purge and *delete interaction detail now*. Each step must stay bounded, delete exactly
the high-fidelity rows, and keep structural evidence.

A small history always runs. The 300k-event acceptance, matching OWG's existing
large-history bar, is opt-in (``OWG_SCALE_ACCEPTANCE=1``) because seeding takes minutes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(r"""
    import json, os, sys, tempfile, time
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    root = Path(tempfile.mkdtemp(prefix="owg-depth-scale-"))
    os.environ.update(WORKFLOW_OBSERVER_DATA=str(root / "data"), WORKFLOW_OBSERVER_AUTH_DIR=str(root / "auth"),
                      WORKFLOW_OBSERVER_CONFIG=str(root / "config.json"))
    sys.path.insert(0, os.getcwd())
    from server import capture_depth, db, interaction_evidence

    total, share = int(sys.argv[1]), float(sys.argv[2])
    db.init_db()
    capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION,
                        "duration_days": 90, "parameters": {"retention_days": 30}})
    now = datetime.now(timezone.utc)
    # Half the history is older than the 30-day retention limit.
    start = now - timedelta(days=60)
    step = 60 * 24 * 3600 / max(total, 1)
    pages = [("app.example", "/inbox"), ("app.example", "/orders"), ("crm.example", "/accounts"), ("docs.example", "/q3")]
    high_every = max(1, round(1 / share)) if share else 0
    timings, counts = {}, {"high_fidelity": 0, "high_fidelity_old": 0, "trajectories": 0}
    batch, high_batch = [], []
    ingest_seconds = 0.0
    for i in range(total):
        at = start + timedelta(seconds=i * step)
        cutoff = now - timedelta(days=30)
        if abs((at - cutoff).total_seconds()) < 3600:
            at = cutoff + timedelta(hours=2)  # keep every event clearly on one side of the retention limit
        host, path = pages[(i // 7) % len(pages)]
        meta = {"page": {"hostname": host, "pathname": path}, "tab_id": i // 500, "source": "browser_extension"}
        event = {"event_id": f"depth-{i:07d}", "session_id": f"s{i // 400}", "observed_at": at.isoformat(),
                 "schema_version": "1.0", "actor_id": "human", "device_id": "device", "sensor_id": "browser:scale",
                 "source": "browser_extension", "app": "Browser", "window_title": path, "duration_seconds": 0.0}
        if high_every and i % high_every == 0:
            kind = ("browser_control_hover_dwell", "browser_drag_start", "browser_pointer_trajectory")[(i // high_every) % 3]
            if kind == "browser_pointer_trajectory":
                meta.update(action="pointer_trajectory", sample_hz=4, coordinates="viewport_fraction",
                            points=[[k * 250, round((k * 37 % 100) / 100, 3), round((k * 53 % 100) / 100, 3)]
                                    for k in range(40)])
                counts["trajectories"] += 1
            elif kind == "browser_drag_start":
                meta.update(action="drag_start", target={"tag": "div", "role": "button"}, payload_kinds=["text"])
            else:
                meta.update(action="control_hover_dwell", target={"tag": "button", "role": "menuitem"},
                            duration_ms=900, ended_by="pointer_left")
            high_batch.append({**event, "event_type": kind, "metadata": meta})
            counts["high_fidelity"] += 1
            counts["high_fidelity_old"] += at < now - timedelta(days=30)
        else:
            meta.update(action="click", target={"tag": "button", "label": "Open"})
            batch.append({**event, "event_type": ("browser_click", "browser_page_view")[i % 2], "metadata": meta})
        if len(batch) >= 5000:
            db.insert_events(batch); batch = []
        if len(high_batch) >= 1000:
            began = time.perf_counter(); db.insert_events(high_batch); ingest_seconds += time.perf_counter() - began
            high_batch = []
    if batch:
        db.insert_events(batch)
    if high_batch:
        began = time.perf_counter(); db.insert_events(high_batch); ingest_seconds += time.perf_counter() - began
    timings["high_fidelity_ingest_per_event_ms"] = ingest_seconds * 1000 / max(counts["high_fidelity"], 1)

    def timed(name, fn):
        began = time.perf_counter(); value = fn(); timings[name] = time.perf_counter() - began; return value

    manifest = timed("coverage_manifest", lambda: interaction_evidence.coverage_manifest(24 * 90))
    friction = timed("friction_report", lambda: interaction_evidence.friction_report(24 * 31))
    pages_out = timed("heatmap_pages", lambda: capture_depth.pointer_heatmap(hours=24 * 90))
    host, path = pages_out["pages"][0]["page"].split("/", 1) if pages_out["pages"] else ("", "")
    grid = timed("heatmap_page", lambda: capture_depth.pointer_heatmap(hostname=host, pathname="/" + path, hours=24 * 90))
    with db.connect() as conn:
        before = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        points_before = conn.execute("SELECT count(*) FROM pointer_trajectory_points").fetchone()[0]
    purged = timed("retention_purge", lambda: capture_depth.apply_retention(force=True))
    deleted = timed("delete_now", lambda: capture_depth.delete_high_fidelity())
    with db.connect() as conn:
        after = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        left_high = conn.execute("SELECT count(*) FROM events WHERE event_type IN ({})".format(
            ",".join("?" for _ in capture_depth.HIGH_FIDELITY_EVENT_TYPES)), capture_depth.HIGH_FIDELITY_EVENT_TYPES).fetchone()[0]
        points_after = conn.execute("SELECT count(*) FROM pointer_trajectory_points").fetchone()[0]
        size = sum(f.stat().st_size for f in (root / "data").glob("workflow_observer.db*"))
    print(json.dumps({"total": total, **counts, "rows_before": before, "rows_after": after,
                      "points_before": points_before, "points_after": points_after, "left_high_fidelity": left_high,
                      "retention_deleted": purged["deleted_events"], "delete_now_deleted": deleted["deleted_events"],
                      "friction_events_considered": friction["events_considered"],
                      "friction_truncated": friction["events_truncated"],
                      "manifest_counts": manifest["high_fidelity_event_counts"],
                      "heatmap_samples": grid.get("samples", 0), "db_bytes": size, "timings": timings}))
""")


def _run(total: int, share: float) -> dict:
    result = subprocess.run([sys.executable, "-c", _SCRIPT, str(total), str(share)], cwd=ROOT, text=True,
                            capture_output=True, timeout=3600)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def _check(out: dict) -> None:
    high = out["high_fidelity"]
    assert out["rows_before"] == out["total"]
    # Retention removes exactly the older high-fidelity rows; delete-now removes the rest; structural rows stay.
    assert out["retention_deleted"] == out["high_fidelity_old"], out
    assert out["retention_deleted"] + out["delete_now_deleted"] == high, out
    assert out["rows_after"] == out["total"] - high and out["left_high_fidelity"] == 0, out
    assert out["points_before"] == out["trajectories"] and out["points_after"] == 0, out
    assert out["manifest_counts"]["browser_pointer_trajectory"] > 0
    assert out["heatmap_samples"] > 0


def test_high_fidelity_capture_stays_bounded_on_a_small_history():
    out = _run(6000, 0.1)
    print(out)
    _check(out)
    t = out["timings"]
    assert t["high_fidelity_ingest_per_event_ms"] < 20, t
    for name in ("coverage_manifest", "friction_report", "heatmap_pages", "heatmap_page", "retention_purge", "delete_now"):
        assert t[name] < 10, (name, t)


@pytest.mark.skipif(os.environ.get("OWG_SCALE_ACCEPTANCE") != "1",
                    reason="set OWG_SCALE_ACCEPTANCE=1 to seed and time a 300k-event history with high-fidelity capture")
def test_high_fidelity_capture_stays_bounded_on_a_300k_event_history():
    total = int(os.environ.get("OWG_SCALE_EVENTS", "300000"))
    out = _run(total, 0.1)
    print(out)
    _check(out)
    t = out["timings"]
    # Generous runner-speed margins. Friction and the heatmap read bounded windows (20k events, 5k batches).
    assert out["friction_events_considered"] <= 20000
    assert t["high_fidelity_ingest_per_event_ms"] < 20, t
    for name in ("coverage_manifest", "friction_report", "heatmap_pages", "heatmap_page"):
        assert t[name] < 30, (name, t)
    for name in ("retention_purge", "delete_now"):
        assert t[name] < 120, (name, t)
