from collector.main import _change_key
from browser_utils import is_browser_app


def test_browser_tab_title_changes_key():
    cfg = {"change_detection": "application"}
    a = {"app": "Google Chrome", "window_title": "Gmail", "excluded": False}
    b = {"app": "Google Chrome", "window_title": "Salesforce", "excluded": False}
    assert _change_key(a, cfg) != _change_key(b, cfg)


def test_unread_counter_does_not_fake_browser_change():
    cfg = {"change_detection": "application"}
    a = {"app": "Google Chrome", "window_title": "(3) Gmail", "excluded": False}
    b = {"app": "Google Chrome", "window_title": "(4) Gmail", "excluded": False}
    assert _change_key(a, cfg) == _change_key(b, cfg)


def test_nonbrowser_title_change_ignored_by_default():
    cfg = {"change_detection": "application"}
    a = {"app": "Finder", "window_title": "Downloads", "excluded": False}
    b = {"app": "Finder", "window_title": "Documents", "excluded": False}
    assert _change_key(a, cfg) == _change_key(b, cfg)


def test_major_browser_families_are_detected():
    names = [
        "Safari", "Safari Technology Preview", "Google Chrome", "Google Chrome Beta",
        "Microsoft Edge", "msedge.exe", "Firefox", "Firefox ESR", "Brave Browser",
        "Arc", "Opera", "Vivaldi", "Orion", "DuckDuckGo", "Mullvad Browser",
        "Tor Browser", "LibreWolf", "Waterfox", "Zen Browser",
    ]
    for name in names:
        assert is_browser_app(name), name


def test_enterprise_can_add_custom_browser_without_code_change():
    cfg = {"change_detection": "application", "browser_app_patterns": ["Acme Secure Browser"]}
    a = {"app": "Acme Secure Browser", "window_title": "ERP", "excluded": False}
    b = {"app": "Acme Secure Browser", "window_title": "CRM", "excluded": False}
    assert _change_key(a, cfg) != _change_key(b, cfg)
