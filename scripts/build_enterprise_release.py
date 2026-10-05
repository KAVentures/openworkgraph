from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def zip_tree(source: Path, target: Path, prefix: str = "") -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.name == "pairing.json" or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(source)
            zf.write(path, str(Path(prefix) / rel) if prefix else str(rel))


def sha(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")


DIST.mkdir(exist_ok=True)
browser = DIST / f"OpenWorkGraph-Browser-Sensor-v{VERSION}.zip"
zip_tree(ROOT / "browser_extension", browser)
sha(browser)

kit_stage = DIST / "enterprise-kit"
if kit_stage.exists():
    shutil.rmtree(kit_stage)
shutil.copytree(ROOT / "enterprise", kit_stage / "enterprise")
for doc in ("SELF_HOSTING.md", "ORGANIZATION_ROLLOUT.md", "DATA_LIFECYCLE.md"):
    shutil.copy2(ROOT / "docs" / doc, kit_stage / doc)
shutil.copy2(browser, kit_stage / browser.name)

kit = DIST / f"OpenWorkGraph-Enterprise-Deployment-Kit-v{VERSION}.zip"
zip_tree(kit_stage, kit)
sha(kit)
shutil.rmtree(kit_stage)
print(browser)
print(kit)
