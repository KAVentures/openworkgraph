from __future__ import annotations

from .secure_runtime import mcp


if __name__ == "__main__":
    mcp.run(transport="stdio")
