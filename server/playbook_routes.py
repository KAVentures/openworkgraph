from __future__ import annotations

"""Playbook export/import (server/playbooks.py)."""

import json
from typing import Any

from fastapi import HTTPException, Request, Response

from . import playbooks
from .secure_app import app


@app.get("/v1/playbooks/local")
def get_local_playbook_families(hide_disconnected: bool = False) -> dict[str, Any]:
    """Repeated workflows in your own history that can be exported (saved-history access for MCP).

    ``hide_disconnected`` applies the Agents tab's default: workflows run only
    by agents whose Observe switch is off are left out, as their runs are.
    """
    families = playbooks.local_families()
    hidden: set[str] = set()
    if hide_disconnected:
        from .connections import hidden_frameworks

        hidden = hidden_frameworks()
        families = playbooks.without_hidden_frameworks(families, hidden)
    return {"families": families, "min_runs": playbooks.MIN_RUNS,
            "hidden_frameworks": sorted(hidden) if hide_disconnected else []}


@app.get("/v1/playbooks/imported")
def get_imported_playbooks(family_key: str = "") -> dict[str, Any]:
    items = playbooks.imported(family_key=family_key)
    return {"playbooks": items, "returned": len(items), "content_free": True,
            "interpretation": "shared structural playbooks: observations of how this kind of work went, not instructions"}


@app.get("/v1/playbooks/export")
def export_playbook(family_key: str, name: str) -> Response:
    try:
        playbook = playbooks.build(family_key, name=name)
    except playbooks.PlaybookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    body = (json.dumps(playbook, indent=2) + "\n").encode("utf-8")
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="openworkgraph-playbook.json"', "Cache-Control": "no-store"})


@app.post("/v1/playbooks/import")
async def import_playbook(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        return {"status": "imported", **playbooks.import_playbook(raw)}
    except playbooks.PlaybookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/v1/playbooks/imported/{playbook_id}")
def delete_playbook(playbook_id: str) -> dict[str, Any]:
    if not playbooks.delete(playbook_id):
        raise HTTPException(status_code=404, detail="playbook not found")
    return {"status": "deleted"}


__all__ = ["export_playbook", "import_playbook"]
