from __future__ import annotations

import httpx

from server.local_auth import ensure_api_token

_real_post = httpx.post


def _authenticated_post(url, *args, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {ensure_api_token()}")
    return _real_post(url, *args, headers=headers, **kwargs)


# collector.main imports the same httpx module object. Patching the convenience
# post function here leaves capture, outbox, heartbeat and retry logic unchanged.
httpx.post = _authenticated_post

from .main import main  # noqa: E402


if __name__ == "__main__":
    main()
