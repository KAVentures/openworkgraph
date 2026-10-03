from __future__ import annotations

"""Pure helpers for deterministic Discovery package summaries."""

from typing import Any


def observed_tools_inventory(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    apps: dict[str, int] = {}
    sites: dict[str, int] = {}
    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue
        for group in bundle.get("canonical_evidence") or []:
            if not isinstance(group, dict):
                continue
            for event in group.get("events") or []:
                if not isinstance(event, dict):
                    continue
                app = str(event.get("app") or "").strip()
                if app and app != "Excluded":
                    apps[app] = apps.get(app, 0) + 1
                meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
                page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
                host = str(page.get("hostname") or "").strip().lower()
                if host:
                    sites[host] = sites.get(host, 0) + 1
    return {
        "apps": [
            {"name": key, "observations": apps[key]}
            for key in sorted(apps)
        ],
        "sites": [
            {"hostname": key, "observations": sites[key]}
            for key in sorted(sites)
        ],
        "caveat": (
            "Observed use does not mean the user or organization has an API, "
            "connector, license, or permission for that tool."
        ),
        "deterministic": True,
    }


__all__ = ["observed_tools_inventory"]
