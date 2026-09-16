from __future__ import annotations

import sys
import types

from collector import accessibility


class _Initializer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Control:
    ControlTypeName = "ButtonControl"
    LocalizedControlType = "button"
    Name = "Approve"
    AutomationId = "approveButton"
    ClassName = "Button"
    HelpText = "Approve this item"
    FrameworkId = "WPF"
    IsPassword = False

    @property
    def Value(self):  # pragma: no cover - should never be read
        raise AssertionError("OpenWorkGraph must not read UIA Value content")


class _PasswordControl(_Control):
    Name = "secret"
    AutomationId = "password"
    IsPassword = True


def _fake_module(control):
    return types.SimpleNamespace(
        UIAutomationInitializerInThread=lambda: _Initializer(),
        ControlFromPoint=lambda _x, _y: control,
    )


def test_windows_control_metadata_never_reads_value(monkeypatch):
    monkeypatch.setitem(sys.modules, "uiautomation", _fake_module(_Control()))
    result = accessibility._windows_element_at_position(10, 20)
    assert result["role"] == "ButtonControl"
    assert result["title"] == "Approve"
    assert result["identifier"] == "approveButton"
    assert result["provider"] == "windows_uiautomation"
    assert "value" not in result


def test_windows_password_control_is_redacted(monkeypatch):
    monkeypatch.setitem(sys.modules, "uiautomation", _fake_module(_PasswordControl()))
    result = accessibility._windows_element_at_position(10, 20)
    assert result["secure"] is True
    assert "title" not in result
    assert "identifier" not in result


def test_unsupported_platform_has_no_semantic_target(monkeypatch):
    monkeypatch.setattr(accessibility.platform, "system", lambda: "Linux")
    assert accessibility.element_at_position(1, 2) == {}
