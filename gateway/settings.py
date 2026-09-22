from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewaySettings:
    database_url: str
    admin_token: str
    enrollment_token: str
    max_batch: int = 500

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
        )
