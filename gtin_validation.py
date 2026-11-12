"""Strict GTIN parsing and check-digit validation."""
from dataclasses import dataclass
import re

SUPPORTED_LENGTHS = {8, 12, 13, 14}


@dataclass(frozen=True)
class GTINResult:
    raw: str
    normalized: str
    status: str


def validate_gtin(value: str | None) -> GTINResult:
    raw = (value or "").strip()
    if not raw or not raw.isascii() or not raw.isdigit() or len(raw) not in SUPPORTED_LENGTHS:
        return GTINResult(raw, "", "INVALID_FORMAT")
    digits = [int(c) for c in raw]
    checksum = (10 - sum(d * (3 if i % 2 == 0 else 1)
                         for i, d in enumerate(reversed(digits[:-1]))) % 10) % 10
    if checksum != digits[-1]:
        return GTINResult(raw, "", "INVALID_CHECK_DIGIT")
    # Canonical 14-digit GTIN preserves identity and leading zeroes.
    return GTINResult(raw, raw.zfill(14), "VALID_CHECKSUM")


def equivalent_lookup_codes(gtin: GTINResult) -> list[str]:
    """Equivalent zero-padded database representations; only for valid GTINs."""
    if gtin.status != "VALID_CHECKSUM":
        return []
    return list(dict.fromkeys((gtin.raw, gtin.normalized)))
