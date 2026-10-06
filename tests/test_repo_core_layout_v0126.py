from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE_MODULES = (
    "browser_privacy",
    "browser_title_privacy",
    "browser_utils",
    "contextualizer",
    "normalizer",
    "resource_references",
    "semantic_actions",
    "sensitive_identifiers",
)


def test_root_core_modules_are_exact_compatibility_aliases():
    for name in CORE_MODULES:
        legacy = importlib.import_module(name)
        canonical = importlib.import_module(f"shared.core.{name}")
        assert legacy is canonical


def test_core_implementations_do_not_depend_on_legacy_root_modules():
    for name in CORE_MODULES:
        text = (ROOT / "shared" / "core" / f"{name}.py").read_text(encoding="utf-8")
        for other in CORE_MODULES:
            assert f"from {other} import " not in text
            assert f"import {other}" not in text


def test_root_core_files_are_compatibility_only():
    for name in CORE_MODULES:
        text = (ROOT / f"{name}.py").read_text(encoding="utf-8")
        assert f"shared.core.{name}" in text
        assert "_sys.modules[__name__] = _impl" in text
        assert len(text.splitlines()) <= 12


def test_runtime_code_uses_canonical_shared_core_imports():
    expected = {
        "collector/business_context.py": "shared.core.resource_references",
        "collector/main.py": "shared.core.sensitive_identifiers",
        "collector/outbox.py": "shared.core.sensitive_identifiers",
        "server/analytics.py": "shared.core.normalizer",
        "server/dashboard_privacy_policy.py": "shared.core.normalizer",
        "server/db.py": "shared.core.browser_privacy",
        "server/evidence_query.py": "shared.core.browser_title_privacy",
        "server/main.py": "shared.core.browser_privacy",
        "server/procedural_feedback.py": "shared.core.semantic_actions",
        "server/typed_privacy_policy.py": "shared.core.sensitive_identifiers",
        "server/v46_migration.py": "shared.core.contextualizer",
        "server/work_profile_service.py": "shared.core.contextualizer",
        "connector/policy.py": "shared.core.sensitive_identifiers",
        "demo_data.py": "shared.core.resource_references",
    }
    for path, import_fragment in expected.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        assert import_fragment in text
