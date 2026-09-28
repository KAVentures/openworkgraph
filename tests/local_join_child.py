"""Runs one step of the employee's local join flow in its own process.

Importing the local enterprise app registers middleware on the shared local
FastAPI app, which would leak into unrelated tests if done in the pytest process.

Usage: python tests/local_join_child.py <config.json> preview|join|me-link|device-token [join_code] [typed_actor]
Prints one JSON object; HTTP errors print {"status": ..., "detail": ...}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    config, action, *rest = sys.argv[1:]
    import server.main as local_main

    local_main.CONFIG_PATH = Path(config)
    import server.enterprise_app as enterprise
    import server.org_join_routes as routes
    from fastapi import HTTPException

    enterprise.CONFIG_PATH = routes.CONFIG_PATH = Path(config)
    routes.restart_sync_worker = lambda _path: None
    try:
        if action == "preview":
            out = routes.org_join_preview(routes.JoinCodeRequest(join_code=rest[0]))
        elif action == "join":
            out = routes.org_join(routes.JoinRequest(join_code=rest[0], actor_id=rest[1] if len(rest) > 1 else "", accept_sharing=True))
        elif action == "me-link":
            out = routes.org_me_link()
        elif action == "device-token":
            from connector.config import load_device_token, load_gateway_settings
            from connector.control import _paths

            _data, auth_dir = _paths(Path(config))
            out = {"token": load_device_token(load_gateway_settings(Path(config), auth_dir=auth_dir))}
        else:
            raise SystemExit(f"unknown action {action}")
    except HTTPException as exc:
        out = {"status": exc.status_code, "detail": exc.detail}
    print(json.dumps(out, default=str))


if __name__ == "__main__":
    main()
