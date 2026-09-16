from __future__ import annotations

from typing import Any


def _string_attr(element: Any, name: str) -> str | None:
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


def element_at_position(x: float, y: float) -> dict[str, Any]:
    """Best-effort semantic description of the macOS UI element at a screen point.

    We intentionally do NOT read AXValue or selected text, because those can contain
    passwords, form contents, message text, or other sensitive user data.
    """
    try:
        import ApplicationServices  # type: ignore

        root = ApplicationServices.AXUIElementCreateSystemWide()
        err, element = ApplicationServices.AXUIElementCopyElementAtPosition(
            root, float(x), float(y), None
        )
        if err != 0 or element is None:
            return {}

        role = _string_attr(element, "AXRole")
        subrole = _string_attr(element, "AXSubrole")
        secure = role == "AXSecureTextField" or subrole == "AXSecureTextField"
        if secure:
            return {"role": role or subrole, "secure": True}

        result = {
            "role": role,
            "subrole": subrole,
            "title": _string_attr(element, "AXTitle"),
            "description": _string_attr(element, "AXDescription"),
            "identifier": _string_attr(element, "AXIdentifier"),
            "help": _string_attr(element, "AXHelp"),
            "secure": False,
        }
        return {k: v for k, v in result.items() if v not in (None, "", False)}
    except Exception:
        return {}
