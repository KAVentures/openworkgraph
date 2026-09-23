from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_api_uses_lifespan_and_preserves_hardening_order():
    source = (ROOT / "server" / "main.py").read_text(encoding="utf-8")

    assert '@app.on_event("startup")' not in source
    assert "@asynccontextmanager\nasync def lifespan" in source
    assert "lifespan=lifespan" in source

    privacy = source.index("initialize_privacy_state()")
    database = source.index("init_db()", privacy)
    identifiers = source.index("harden_existing_sensitive_identifiers_v46()", database)
    browser = source.index("harden_existing_browser_events(_runtime_config())", identifiers)
    yielded = source.index("yield", browser)

    assert privacy < database < identifiers < browser < yielded
