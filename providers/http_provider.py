"""Shared HTTP behavior for the publicly documented Open Facts APIs."""
import time
import requests
from .base import ProviderResult, utc_now


class FactsProvider:
    source = ""
    base_url = ""

    def __init__(self, delay: float = .7, timeout: float = 15, session=None):
        self.delay, self.timeout = max(0, delay), timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "BarcodeAudit/1.0 (shop inventory check)"})

    def lookup(self, code: str) -> ProviderResult:
        url = f"{self.base_url}/api/v2/product/{code}.json"
        params = {"fields": "product_name,brands,quantity,brand_owner,manufacturing_places,product_type"}
        error = ""
        for attempt in range(4):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 404:
                    return ProviderResult(self.source, "NOT_FOUND_IN_SOURCE", retrieved_at=utc_now())
                if response.status_code == 429 or response.status_code >= 500:
                    error = f"HTTP {response.status_code}"
                    if attempt < 3:
                        retry = response.headers.get("Retry-After", "")
                        try: wait = min(60, float(retry))
                        except ValueError: wait = 2 ** attempt
                        time.sleep(wait)
                        continue
                if response.status_code >= 400:
                    return ProviderResult(self.source, "ERROR", retrieved_at=utc_now(),
                                          error=f"HTTP {response.status_code}")
                response.raise_for_status()
                data = response.json()
                if data.get("status") != 1:
                    return ProviderResult(self.source, "NOT_FOUND_IN_SOURCE", retrieved_at=utc_now())
                p = data.get("product") or {}
                return ProviderResult(self.source, "FOUND", str(p.get("product_name") or ""),
                    str(p.get("brands") or ""), str(p.get("quantity") or ""),
                    str(p.get("brand_owner") or ""), utc_now(), raw=p)
            except requests.RequestException as exc:
                error = f"{type(exc).__name__}: {exc}"
                if attempt < 3:
                    time.sleep(2 ** attempt)
            except (ValueError, TypeError) as exc:
                error = f"Invalid JSON response: {exc}"
                break
            finally:
                if self.delay: time.sleep(self.delay)
        return ProviderResult(self.source, "ERROR", retrieved_at=utc_now(), error=error or "Unknown provider error")
