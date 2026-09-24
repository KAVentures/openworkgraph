from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path


_VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
if _VERSION_FILE.exists():
    PRODUCT_VERSION = _VERSION_FILE.read_text(encoding="utf-8").strip()
else:
    try:
        PRODUCT_VERSION = distribution_version("workflow-observer")
    except PackageNotFoundError:
        PRODUCT_VERSION = "0.0.0"


@dataclass(frozen=True)
class GatewaySettings:
    database_url: str
    admin_token: str
    enrollment_token: str
    max_batch: int = 500
    max_event_bytes: int = 262_144
    max_batch_bytes: int = 8_388_608

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        return cls(
            database_url=os.getenv(
                "OWG_GATEWAY_DATABASE_URL",
                "sqlite:///./data/gateway/openworkgraph_gateway.db",
            ),
            admin_token=os.getenv("OWG_GATEWAY_ADMIN_TOKEN", ""),
            enrollment_token=os.getenv("OWG_GATEWAY_ENROLLMENT_TOKEN", ""),
            max_batch=max(1, min(int(os.getenv("OWG_GATEWAY_MAX_BATCH", "500")), 5000)),
            max_event_bytes=max(16_384, min(int(os.getenv("OWG_GATEWAY_MAX_EVENT_BYTES", "262144")), 4_194_304)),
            max_batch_bytes=max(262_144, min(int(os.getenv("OWG_GATEWAY_MAX_BATCH_BYTES", "8388608")), 67_108_864)),
        )
