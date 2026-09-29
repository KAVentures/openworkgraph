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
    import server.outcome_routes  # noqa: F401
    import server.agent_brief_routes  # noqa: F401

    import server.agent_capture_runtime as agent_capture_runtime
    import server.log_redaction as log_redaction

    log_redaction.install()

    org_join_routes.start_managed_setup_in_background()
    agent_capture_runtime.start()
    import server.outcome_tracker as outcome_tracker

    if not agent_capture_runtime._demo():
        outcome_tracker.start()  # does nothing until the person turns outcome tracking on
    # uvicorn re-raises SIGTERM/SIGINT after its graceful shutdown, which ends
    # the process before a finally/atexit block can run. Revoke the recording
    # lease in the app's own shutdown step so a normal quit stops agent spooling
    # at once (a hard kill is still bounded by the lease's short TTL).
    from shared.lifespan import extend_lifespan
    from server.secure_app import app as secure_app

    extend_lifespan(secure_app, shutdown=agent_capture_runtime.stop)
    extend_lifespan(secure_app, shutdown=outcome_tracker.stop)
    try:
        uvicorn.run(SECURE_APP, host=args.host, port=args.port)
    finally:
        agent_capture_runtime.stop()
        outcome_tracker.stop()


if __name__ == "__main__":
    main()
