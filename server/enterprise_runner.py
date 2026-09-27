from __future__ import annotations

import argparse

import uvicorn


SECURE_APP = "server.secure_app:app"


def main() -> None:
    """Run the established secure local app with optional enterprise controls installed.

    Importing the additive route modules registers Gateway/capture/deletion routes,
    lifecycle hooks and dashboard controls on the same FastAPI app object exported
    by ``server.secure_app``. Uvicorn still serves the hardened secure-app target.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    # Registration imports: these modules extend, rather than replace, secure_app.
    import server.enterprise_app  # noqa: F401
    import server.history_routes  # noqa: F401
    import server.history_capture_integration  # noqa: F401
    import server.evidence_delete_routes  # noqa: F401
    import server.v0571_polish  # noqa: F401
    import server.evidence_paging  # noqa: F401
    import server.work_profile_routes  # noqa: F401
    import server.context_pulse_routes  # noqa: F401
    import server.browser_signal_routes  # noqa: F401
    import server.agent_dashboard_control_plane  # noqa: F401
    import server.org_join_routes as org_join_routes
    # Import last so its HTML middleware injects the privacy-safe dashboard loader
    # after the other additive dashboard scripts have been installed.
    import server.dashboard_privacy  # noqa: F401

    # Managed setup is best-effort and never blocks local capture or the dashboard.
    # Its status is always exposed in the employee UI when a managed config exists.
    org_join_routes.start_managed_setup_in_background()
    uvicorn.run(SECURE_APP, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
