from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Test storage must be selected before test modules import server.db because
# server.db fixes DATA_DIR/DB_PATH at import time.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="owg-test-"))
_LOCAL_DATA = _TEST_ROOT / "local"
_GATEWAY_DATA = _TEST_ROOT / "gateway"
_AUTH_DATA = _TEST_ROOT / "auth"
for path in (_LOCAL_DATA, _GATEWAY_DATA, _AUTH_DATA):
    path.mkdir(parents=True, exist_ok=True)

os.environ["WORKFLOW_OBSERVER_DATA"] = str(_LOCAL_DATA)
os.environ["WORKFLOW_OBSERVER_AUTH_DIR"] = str(_AUTH_DATA)
os.environ["OWG_GATEWAY_DATABASE_URL"] = f"sqlite:///{_GATEWAY_DATA / 'openworkgraph_gateway.db'}"


@pytest.fixture(autouse=True)
def _isolate_agent_ingestion_database(request):
    """Keep new agent-ingestion persistence tests from polluting legacy analytics tests.

    The established test suite shares one temporary DB within a pytest process.
    Agent-ingestion tests intentionally write canonical rows, so give only that
    module its own DB and clear the append-only analytics cache at both boundaries.
    """
    if Path(str(request.fspath)).name != "test_agent_ingestion_v059.py":
        yield
        return

    from server import analytics
    from server import db as server_db

    original = server_db.DB_PATH
    isolated_dir = Path(tempfile.mkdtemp(prefix="owg-agent-test-", dir=str(_TEST_ROOT)))
    server_db.DB_PATH = isolated_dir / "agent-evidence.db"
    analytics.clear_summary_cache()
    server_db.init_db()
    try:
        yield
    finally:
        analytics.clear_summary_cache()
        server_db.DB_PATH = original
