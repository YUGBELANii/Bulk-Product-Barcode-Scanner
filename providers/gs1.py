"""Credential-gated GS1 provider placeholder; no undocumented endpoint is used."""
import os


class GS1Provider:
    source = "GS1"

    @staticmethod
    def enabled() -> bool:
        # API integration remains disabled until the user supplies authorized
        # credentials AND implementation details from the service documentation.
        return bool(os.getenv("GS1_API_BASE_URL") and os.getenv("GS1_API_TOKEN"))

    def lookup(self, code: str):
        raise RuntimeError("GS1 API access is not configured; obtain authorized API documentation and credentials first")
