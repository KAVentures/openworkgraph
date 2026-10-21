from __future__ import annotations

import ast
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "server" / "secure_app.py"
FUNCTIONS = {"_bootstrap_script", "_connection_override_script"}


def constant_return(fn: ast.FunctionDef) -> str:
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise RuntimeError(f"{fn.name} must return a literal script string so CI can syntax-check it")


def main() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    scripts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            value = constant_return(node).strip()
            if not (value.startswith("<script>") and value.endswith("</script>")):
                raise RuntimeError(f"{node.name} did not return a <script> block")
            scripts[node.name] = value[len("<script>") : -len("</script>")]
    missing = FUNCTIONS - scripts.keys()
    if missing:
        raise RuntimeError(f"missing injected dashboard script functions: {sorted(missing)}")

    # Nonce-bearing organization process shell is packaged independently of the
    # local dashboard. Check its actual shipped script, too.
    from html.parser import HTMLParser

    class InlineScripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inside = False
            self.parts: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == 'script':
                self.inside = True

        def handle_endtag(self, tag):
            if tag == 'script':
                self.inside = False

        def handle_data(self, data):
            if self.inside:
                self.parts.append(data)

    portal = InlineScripts()
    portal.feed((ROOT / 'gateway' / 'processes.html').read_text(encoding='utf-8'))
    if not portal.parts:
        raise RuntimeError('Organization process portal script is missing')
    scripts['organization_process_portal'] = '\n'.join(portal.parts)

    with tempfile.TemporaryDirectory() as tmp:
        for name, javascript in scripts.items():
            path = Path(tmp) / f"{name}.js"
            path.write_text(javascript, encoding="utf-8")
            subprocess.run(["node", "--check", str(path)], check=True)
    print("Authenticated dashboard injected JavaScript syntax: OK")


if __name__ == "__main__":
    main()
