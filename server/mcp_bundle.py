from __future__ import annotations

import io
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "integrations" / "mcpb"


def claude_mcpb_bytes() -> bytes:
    """Build the tiny Claude Desktop MCPB wrapper from the installed sources."""
    manifest = SOURCE / "manifest.json"
    launcher = SOURCE / "server" / "index.js"
    if not manifest.is_file() or not launcher.is_file():
        raise FileNotFoundError("Claude MCP bundle sources are not installed")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest, "manifest.json")
        zf.write(launcher, "server/index.js")
    return out.getvalue()
