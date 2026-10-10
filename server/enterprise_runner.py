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
    import server.ephemeral_page_routes  # noqa: F401
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
    import server.workflow_evidence_routes  # noqa: F401
    import server.workflow_evidence_dashboard  # noqa: F401
    # Last: its dashboard script arranges what the other layers built (Basic/Advanced).
    import server.basic_mode_routes  # noqa: F401

    import server.agent_capture_runtime as agent_capture_runtime
    import server.agent_session_sensor as agent_session_sensor
    import server.agent_working_detail as agent_working_detail
    import server.log_redaction as log_redaction
    import server.outcome_tracker as outcome_tracker
    from server.agent_session_store import init_agent_session_store

    log_redaction.install()

    # Database/schema/privacy initialization lives in the app's existing lifespan.
    # Background workers used to start before uvicorn entered that lifespan, which
    # let Windows race a fresh SQLite open against init_db()/PRAGMA journal_mode=WAL.
    # Keep startup two-phase: first finish every SQLite-backed schema setup while no
    # worker thread exists, then start the workers.
    from shared.lifespan import extend_lifespan
    from server.secure_app import app as secure_app

    def _start_background_runtime() -> None:
        # The canonical DB has already been initialized by server.main.lifespan.
        # Prepare additive stores synchronously before any scanner/spool worker thread can
        # touch SQLite. These initializers are idempotent.
        init_agent_session_store()
        agent_working_detail.init_store()

        # Working detail waits before its first scan; the native-session scanner and
        # spool runtime may run immediately, but all required schemas now exist and
        # WAL mode has already been established by init_db().
        agent_working_detail.start()
        agent_session_sensor.start()
        agent_capture_runtime.start()
        org_join_routes.start_managed_setup_in_background()
        if not agent_capture_runtime._demo():
            outcome_tracker.start()  # does nothing until the person turns outcome tracking on

    extend_lifespan(secure_app, startup=_start_background_runtime)

    # Keep shutdown hooks separate. Besides making the lifecycle explicit, this
    # preserves the established normal-quit guarantee that the agent recording
    # lease is revoked inside FastAPI shutdown rather than relying on finally/atexit.
    extend_lifespan(secure_app, shutdown=agent_capture_runtime.stop)
    extend_lifespan(secure_app, shutdown=agent_session_sensor.stop)
    extend_lifespan(secure_app, shutdown=agent_working_detail.stop)
    extend_lifespan(secure_app, shutdown=outcome_tracker.stop)

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
