from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mcpb"
DIST = ROOT / "dist"
OUTPUT = DIST / "OpenWorkGraph-Claude.mcpb"


def main() -> None:
    manifest_path = SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = ("manifest_version", "name", "version", "description", "author", "server")
    missing = [key for key in required if not manifest.get(key)]
    if missing:
        raise SystemExit(f"MCPB manifest missing required fields: {', '.join(missing)}")
    if str(manifest.get("manifest_version")) != "0.3":
        raise SystemExit("OpenWorkGraph currently targets MCPB manifest_version 0.3")
    if manifest.get("server", {}).get("entry_point") != "server/index.js":
        raise SystemExit("MCPB entry_point must be server/index.js")

    DIST.mkdir(parents=True, exist_ok=True)
    OUTPUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest_path, "manifest.json")
        zf.write(SOURCE / "server" / "index.js", "server/index.js")
    print(f"Built: {OUTPUT}")


if __name__ == "__main__":
    main()
