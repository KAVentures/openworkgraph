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
    import server.evidence_delete_routes  # noqa: F401
    import server.v0571_polish  # noqa: F401
    import server.evidence_paging  # noqa: F401
    import server.work_profile_routes  # noqa: F401
    import server.browser_signal_routes  # noqa: F401

    uvicorn.run(SECURE_APP, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
