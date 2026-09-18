from __future__ import annotations

import platform
from typing import Any


ROW_LABEL_MAX_CHARS = 80
ROW_LABEL_MAX_WORDS = 12


def _clean_text(value: Any, *, limit: int = 240) -> str | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    if not text:
        return None
    return text[:limit]


def _row_like_role(*values: Any) -> bool:
    text = " ".join(str(v or "") for v in values).casefold()
    return any(marker in text for marker in (
        "axrow", "axcell", "axlist", "axoutline",
        "listitem", "dataitem", "treeitem", "table row", "gridcell",
    ))


def _safe_native_label(value: Any, *, row_like: bool) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if row_like and (
        len(text) > ROW_LABEL_MAX_CHARS
        or len(text.split()) > ROW_LABEL_MAX_WORDS
    ):
        return None
    return text


def _mac_string_attr(element: Any, name: str) -> str | None:
    try:
        import ApplicationServices  # type: ignore

        err, value = ApplicationServices.AXUIElementCopyAttributeValue(element, name, None)
        if err != 0 or value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if value.__class__.__name__ in {"NSCFString", "__NSCFString", "NSString"}:
            text = str(value).strip()
            return text or None
    except Exception:
        return None
    return None


def _mac_element_at_position(x: float, y: float) -> dict[str, Any]:
    try:
        import ApplicationServices  # type: ignore

        root = ApplicationServices.AXUIElementCreateSystemWide()
        err, element = ApplicationServices.AXUIElementCopyElementAtPosition(
            root, float(x), float(y), None
        )
        if err != 0 or element is None:
            return {}

        role = _mac_string_attr(element, "AXRole")
        subrole = _mac_string_attr(element, "AXSubrole")
        secure = role == "AXSecureTextField" or subrole == "AXSecureTextField"
        if secure:
            return {"role": role or subrole, "secure": True}

        row_like = _row_like_role(role, subrole)
        result = {
            "role": role,
            "subrole": subrole,
            "title": _safe_native_label(_mac_string_attr(element, "AXTitle"), row_like=row_like),
            "description": _safe_native_label(_mac_string_attr(element, "AXDescription"), row_like=row_like),
            "identifier": _mac_string_attr(element, "AXIdentifier"),
            "help": _safe_native_label(_mac_string_attr(element, "AXHelp"), row_like=row_like),
            "secure": False,
            "provider": "macos_accessibility",
        }
        return {k: v for k, v in result.items() if v not in (None, "", False)}
    except Exception:
        return {}


def _windows_property(control: Any, name: str) -> Any:
    try:
        return getattr(control, name)
    except Exception:
        return None


def _windows_element_at_position(x: float, y: float) -> dict[str, Any]:
    """Best-effort UI Automation metadata for the native control at a point.

    Only control identity/label properties are read. We deliberately never request
    ValuePattern, TextPattern, LegacyIAccessibleValue or other properties that can
    expose what a user typed into a field.
    """
    try:
        import uiautomation as auto  # type: ignore

        # Interaction capture runs in its own worker thread. The package requires
        # COM/UIAutomation initialization in each thread that reads controls.
        with auto.UIAutomationInitializerInThread():
            control = auto.ControlFromPoint(int(round(x)), int(round(y)))
            if control is None:
                return {}

            secure = bool(_windows_property(control, "IsPassword"))
            role = _clean_text(_windows_property(control, "ControlTypeName"))
            localized_role = _clean_text(_windows_property(control, "LocalizedControlType"))
            if secure:
                return {
                    "role": role or localized_role or "password",
                    "secure": True,
                    "provider": "windows_uiautomation",
                }

            row_like = _row_like_role(role, localized_role)
            result = {
                "role": role,
                "localized_role": localized_role,
                "title": _safe_native_label(_windows_property(control, "Name"), row_like=row_like),
                "identifier": _clean_text(_windows_property(control, "AutomationId")),
                "class_name": _clean_text(_windows_property(control, "ClassName")),
                "help": _safe_native_label(_windows_property(control, "HelpText"), row_like=row_like),
                "framework": _clean_text(_windows_property(control, "FrameworkId")),
                "secure": False,
                "provider": "windows_uiautomation",
            }
            return {k: v for k, v in result.items() if v not in (None, "", False)}
    except Exception:
        # UIA support is intentionally best effort: applications running at a
        # higher integrity level or apps without a UIA provider may return no label.
        return {}


def element_at_position(x: float, y: float) -> dict[str, Any]:
    """Return privacy-conscious semantic metadata for the UI element at a point.

    macOS uses Accessibility and Windows uses Microsoft UI Automation. Neither path
    reads typed field values, selected text, clipboard contents or password values.
    Oversized list/row labels are omitted because those frequently contain an entire
    mail, chat, record, or patient row rather than a control name. Unsupported
    platforms simply return no semantic target metadata.
    """
    system = platform.system()
    if system == "Darwin":
        return _mac_element_at_position(x, y)
    if system == "Windows":
        return _windows_element_at_position(x, y)
    return {}