from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import db as server_db
from shared.lifespan import extend_lifespan


ROOT = Path(__file__).resolve().parents[1]


def test_pytest_storage_is_isolated_from_repository_data() -> None:
    local_data = Path(os.environ["WORKFLOW_OBSERVER_DATA"]).resolve()
    auth_data = Path(os.environ["WORKFLOW_OBSERVER_AUTH_DIR"]).resolve()
    gateway_url = os.environ["OWG_GATEWAY_DATABASE_URL"]
    active_db = server_db.DB_PATH.resolve()

    assert ROOT.resolve() not in local_data.parents
    assert ROOT.resolve() not in auth_data.parents
    # Some existing tests deliberately redirect DB_PATH again to pytest's own
    # tmp_path. That is stronger isolation and must remain valid; the invariant
    # is that no active test DB ever points back into the repository data tree.
    assert ROOT.resolve() not in active_db.parents
    assert active_db != (ROOT / "data" / "workflow_observer.db").resolve()
    assert "owg-test-" in str(local_data)
    assert "owg-test-" in gateway_url
    assert str(ROOT / "data") not in gateway_url


def test_reported_server_modules_no_longer_use_deprecated_on_event() -> None:
    for relative in (
        "server/secure_app.py",
        "server/enterprise_app.py",
        "gateway/app.py",
        "gateway/enterprise_app.py",
        "gateway/human_access.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert ".on_event(" not in text, relative


def test_composed_lifespan_preserves_startup_and_reverse_shutdown_order() -> None:
    events: list[str] = []
    app = FastAPI()

    extend_lifespan(
        app,
        startup=lambda: events.append("outer-start"),
        shutdown=lambda: events.append("outer-stop"),
    )
    extend_lifespan(
        app,
        startup=lambda: events.append("inner-start"),
        shutdown=lambda: events.append("inner-stop"),
    )

    @app.get("/health")
    def health():
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert events == ["outer-start", "inner-start"]

    assert events == ["outer-start", "inner-start", "inner-stop", "outer-stop"]
