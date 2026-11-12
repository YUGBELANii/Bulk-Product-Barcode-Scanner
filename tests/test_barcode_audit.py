import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import requests

from comparison import compare_product, quantity_match
from company_comparison import compare_company
from gtin_validation import validate_gtin, equivalent_lookup_codes
from providers.http_provider import FactsProvider
from check_barcodes_v2 import process


class GTINTests(unittest.TestCase):
    def test_supported_valid_codes_and_zero_preservation(self):
        expected = {"96385074": 8, "036000291452": 12, "4006381333931": 13, "10012345000017": 14}
        for code in expected:
            with self.subTest(code=code):
                result = validate_gtin(code)
                self.assertEqual(result.status, "VALID_CHECKSUM")
                self.assertEqual(len(result.raw), expected[code])
                self.assertTrue(result.normalized.endswith(result.raw))
        self.assertEqual(validate_gtin("036000291452").raw[0], "0")

    def test_invalid_format_and_invalid_check_digit_are_separate(self):
        self.assertEqual(validate_gtin("123").status, "INVALID_FORMAT")
        self.assertEqual(validate_gtin("4006381333932").status, "INVALID_CHECK_DIGIT")
        self.assertEqual(validate_gtin("400-638-1333931").status, "INVALID_FORMAT")

    def test_only_valid_codes_get_equivalent_lookup_candidates(self):
        self.assertEqual(equivalent_lookup_codes(validate_gtin("036000291452")), ["036000291452", "00036000291452"])
        self.assertEqual(equivalent_lookup_codes(validate_gtin("4006381333932")), [])


class ComparisonTests(unittest.TestCase):
    def test_generic_shared_words_do_not_override_brand_conflict(self):
        result = compare_product("Dove Anti-Dandruff Shampoo", "Dove", "200 ml",
                                 "Head & Shoulders Anti-Dandruff Shampoo", "Head & Shoulders", "200 ml")
        self.assertEqual(result.identity, "MISMATCH")
        self.assertEqual(result.brand, "MISMATCH")

    def test_product_identity_and_quantity_are_separate(self):
        result = compare_product("Cadbury Bournvita Health Drink", "Cadbury", "200 g",
                                 "Cadbury Dairy Milk Chocolate", "Cadbury", "500 g")
        self.assertEqual(result.identity, "MISMATCH")
        self.assertEqual(result.quantity, "MISMATCH")
        self.assertEqual(quantity_match("1 L", "1000 ml"), "MATCH")

    def test_parent_company_matching_is_conservative(self):
        self.assertEqual(compare_company("Mondelez International", "Mondelez International"), "MATCH")
        self.assertEqual(compare_company("Mondelez International", "Mondelez International Inc."), "POSSIBLE_MATCH")
        self.assertEqual(compare_company("Mondelez International", "Acme Imports"), "POSSIBLE_MISMATCH")
        self.assertEqual(compare_company("", "Acme"), "UNKNOWN")


class ProviderTests(unittest.TestCase):
    def test_http_failure_is_error_not_not_found(self):
        class Response:
            status_code = 503
            headers = {}
            def raise_for_status(self): raise requests.HTTPError("server down")
        class Session:
            headers = {}
            def update(self, *args, **kwargs): pass
            def get(self, *args, **kwargs): return Response()
        provider = FactsProvider(delay=0, session=Session())
        provider.source, provider.base_url = "Test", "https://example.invalid"
        with patch("providers.http_provider.time.sleep"):
            got = provider.lookup("4006381333931")
        self.assertEqual(got.status, "ERROR")
        self.assertIn("HTTP 503", got.error)

    def test_missing_database_record_is_explicit_not_found(self):
        class Response:
            status_code = 200
            headers = {}
            def raise_for_status(self): pass
            def json(self): return {"status": 0}
        class Session:
            headers = {}
            def update(self, *args, **kwargs): pass
            def get(self, *args, **kwargs): return Response()
        provider = FactsProvider(delay=0, session=Session())
        provider.source, provider.base_url = "Test", "https://example.invalid"
        self.assertEqual(provider.lookup("4006381333931").status, "NOT_FOUND_IN_SOURCE")


class CSVAuditTests(unittest.TestCase):
    def test_rows_preserved_duplicates_and_missing_gtin_flags_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp, out = root / "input.csv", root / "out"
            with inp.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["name", "barcode", "packSize", "brand", "parent_company"])
                w.writeheader()
                w.writerows([
                    {"name": "Example Drink", "barcode": "4006381333931", "packSize": "200 g", "brand": "Example", "parent_company": "Example Co"},
                    {"name": "Other Product", "barcode": "4006381333931", "packSize": "500 g", "brand": "Other", "parent_company": "Other Co"},
                    {"name": "Invalid", "barcode": "4006381333932", "packSize": "", "brand": "X", "parent_company": "Y"},
                    {"name": "Blank", "barcode": "", "packSize": "", "brand": "X", "parent_company": "Y"},
                ])
            result = process(inp, out, [], 0, False, offline=True)
            with (out / "results.csv").open(encoding="utf-8") as f: saved = list(csv.DictReader(f))
            self.assertEqual(len(saved), 4)
            self.assertEqual([r["name"] for r in saved], ["Example Drink", "Other Product", "Invalid", "Blank"])
            self.assertTrue(all("DUPLICATE_GTIN" in r["findings"] for r in saved[:2]))
            self.assertEqual(saved[2]["barcode_status"], "INVALID_CHECK_DIGIT")
            self.assertEqual(saved[3]["barcode_status"], "INVALID_FORMAT")
            self.assertTrue((out / "flagged_products.csv").exists())
            self.assertTrue((out / "source_evidence.csv").exists())
            self.assertTrue((out / "audit_summary.txt").exists())

    def test_duplicate_valid_gtin_uses_one_provider_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inp = root / "input.csv"; out = root / "out"
            with inp.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["name", "barcode"]); w.writeheader()
                w.writerows([{"name": "One", "barcode": "4006381333931"}, {"name": "Two", "barcode": "4006381333931"}])
            class FakeProvider:
                source = "OpenFoodFacts"
                calls = 0
                def __init__(self, **kwargs): pass
                def lookup(self, code):
                    type(self).calls += 1
                    from providers.base import ProviderResult
                    return ProviderResult(self.source, "NOT_FOUND_IN_SOURCE")
            with patch("check_barcodes_v2.OpenFoodFactsProvider", FakeProvider):
                process(inp, out, ["OpenFoodFacts"], 0, False)
            self.assertEqual(FakeProvider.calls, 1)


if __name__ == "__main__":
    unittest.main()
