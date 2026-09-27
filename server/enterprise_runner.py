from __future__ import annotations

import argparse
import uvicorn

SECURE_APP = "server.secure_app:app"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    import server.enterprise_app  # noqa: F401
    import server.history_routes  # noqa: F401
    import server.history_capture_integration  # noqa: F401
    import server.history_ai_access_integration  # noqa: F401
    import server.evidence_delete_routes  # noqa: F401
    import server.v0571_polish  # noqa: F401
    import server.evidence_paging  # noqa: F401
    import server.work_profile_routes  # noqa: F401
    import server.context_pulse_routes  # noqa: F401
    import server.browser_signal_routes  # noqa: F401
    import server.browser_agent_projection  # noqa: F401
    import server.agent_dashboard_control_plane  # noqa: F401
    import server.custom_harness_control_plane  # noqa: F401
    import server.first_value_activation  # noqa: F401
    import server.org_join_routes as org_join_routes
    import server.dashboard_privacy  # noqa: F401

    org_join_routes.start_managed_setup_in_background()
    uvicorn.run(SECURE_APP, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
