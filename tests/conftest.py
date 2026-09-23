from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
