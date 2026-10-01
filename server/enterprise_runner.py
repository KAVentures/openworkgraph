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
    import server.playbook_routes  # noqa: F401
    import server.agent_session_routes  # noqa: F401

    import server.agent_capture_runtime as agent_capture_runtime
    import server.agent_session_sensor as agent_session_sensor
    import server.agent_working_detail as agent_working_detail
    import server.log_redaction as log_redaction
    import server.outcome_tracker as outcome_tracker

    log_redaction.install()

    # Database/schema/privacy initialization lives in the app's existing lifespan.
    # Start every background subsystem only after that lifespan has entered. Starting
    # these threads before uvicorn previously allowed Windows to race a fresh SQLite
    # database open against init_db()/PRAGMA journal_mode=WAL, intermittently causing
    # `sqlite3.OperationalError: database is locked` during application startup.
    from shared.lifespan import extend_lifespan
    from server.secure_app import app as secure_app

    extend_lifespan(secure_app, startup=org_join_routes.start_managed_setup_in_background)
    extend_lifespan(
        secure_app,
        startup=agent_capture_runtime.start,
        shutdown=agent_capture_runtime.stop,
    )
    extend_lifespan(
        secure_app,
        startup=agent_session_sensor.start,
        shutdown=agent_session_sensor.stop,
    )
    # Working detail is a separate, opt-in continuity layer. Its scanner has an
    # independent state/cursor and cannot interfere with structural capture.
    extend_lifespan(
        secure_app,
        startup=agent_working_detail.start,
        shutdown=agent_working_detail.stop,
    )

    def _start_outcome_tracker() -> None:
        if not agent_capture_runtime._demo():
            outcome_tracker.start()  # does nothing until the person turns outcome tracking on

    extend_lifespan(
        secure_app,
        startup=_start_outcome_tracker,
        shutdown=outcome_tracker.stop,
    )

    # uvicorn re-raises SIGTERM/SIGINT after its graceful shutdown, which ends
    # the process before a finally/atexit block can run. The lifespan hooks above
    # therefore stop runtime workers during a normal quit; this finally remains a
    # defensive idempotent fallback for startup failures and other exits.
    try:
        uvicorn.run(SECURE_APP, host=args.host, port=args.port)
    finally:
        agent_capture_runtime.stop()
        agent_session_sensor.stop()
        agent_working_detail.stop()
        outcome_tracker.stop()


if __name__ == "__main__":
    main()
