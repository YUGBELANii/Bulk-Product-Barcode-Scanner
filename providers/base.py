from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class ProviderResult:
    source: str
    status: str
    product_name: str = ""
    brand: str = ""
    quantity: str = ""
    company: str = ""
    retrieved_at: str = ""
    error: str = ""
    raw: dict[str, Any] | None = None

    def record(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
