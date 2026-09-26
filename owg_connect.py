"""Connect AI clients to OpenWorkGraph and switch them on/off (prints JSON).

Works from any directory, so people and agents can run it directly:

    <OpenWorkGraph python> <OpenWorkGraph root>/owg_connect.py list
    <OpenWorkGraph python> <OpenWorkGraph root>/owg_connect.py on claude_code
    <OpenWorkGraph python> <OpenWorkGraph root>/owg_connect.py off cursor --mcp
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from server.connections import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
