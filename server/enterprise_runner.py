from __future__ import annotations

import argparse

import uvicorn


SECURE_APP = "server.secure_app:app"


def main() -> None:
    """Run the established secure local app with optional Gateway controls installed.

    Importing ``server.enterprise_app`` registers the additive Gateway routes,
    lifecycle hooks and dashboard panel on the same FastAPI app object exported by
    ``server.secure_app``. Uvicorn still serves the hardened secure-app target.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    # Registration import: this module extends, rather than replaces, secure_app.
    import server.enterprise_app  # noqa: F401

    uvicorn.run(SECURE_APP, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
