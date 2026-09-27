from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Launch the compact local MCP server from the installed OpenWorkGraph root.

    MCP clients may start this script from any working directory. Keep all
    installation-specific path knowledge here so saved client configurations do
    not have to know OpenWorkGraph's module layout or authentication directory.
    """
    # Saved client configs pass "--client <id>" so the dashboard's per-client
    # on/off switch can be enforced on every tool call.
    argv = sys.argv[1:]
    if "--client" in argv:
        index = argv.index("--client")
        if index + 1 < len(argv) and argv[index + 1]:
            os.environ["OWG_MCP_CLIENT"] = argv[index + 1]
        del sys.argv[1 + index:1 + index + 2]
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(ROOT)
    os.environ.setdefault("PYTHONPATH", root)
    os.environ.setdefault("WORKFLOW_OBSERVER_API", "http://127.0.0.1:8787")
    os.environ.setdefault("WORKFLOW_OBSERVER_AUTH_DIR", str(ROOT / "data" / "auth"))

    from mcp_server.compact_stdio import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
