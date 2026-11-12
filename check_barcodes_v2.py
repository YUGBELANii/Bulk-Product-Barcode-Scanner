#!/usr/bin/env python3
"""Evidence-based barcode and product identity audit CLI."""
import argparse
import csv
import json
import logging
import os
import re
import sys
import time
import zipfile
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from comparison import compare_product
from company_comparison import compare_company
from gtin_validation import validate_gtin
from providers.base import ProviderResult
from providers.open_food_facts import OpenFoodFactsProvider
from providers.open_beauty_facts import OpenBeautyFactsProvider
from providers.open_products_facts import OpenProductsFactsProvider

LOG = logging.getLogger("barcode_audit")
TTL = {"FOUND": 30 * 86400, "NOT_FOUND_IN_SOURCE": 86400, "ERROR": 300}
OUTPUT_FIELDS = ["barcode_status", "product_match_score", "brand_match", "parent_company_match",
                 "quantity_match", "verification_status", "confidence", "mismatch_reason",
                 "sources_checked", "sources_found", "evidence", "findings", "external_product_name",
                 "external_brand", "external_manufacturer", "external_brand_owner", "external_quantity",
                 "source_database", "recommended_investigation"]


def json_load(path: Path, default: Any):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError): return default


def read_rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames: raise ValueError("Input CSV has no header")
        rows = list(reader)
    return reader.fieldnames, rows


def source_class(provider: Any) -> str:
    return provider.source


def cached_lookup(code: str, provider: Any, cache: dict, use_cache: bool, cache_identity: str | None = None) -> ProviderResult:
    key = f"{provider.source}:{cache_identity or code}"
    item = cache.get(key)
    if use_cache and item:
        age = time.time() - float(item.get("cached_at", 0))
        if age < TTL.get(item.get("result", {}).get("status"), 60):
            return ProviderResult(**{k: v for k, v in item["result"].items() if k in ProviderResult.__dataclass_fields__})
    result = provider.lookup(code)
    cache[key] = {"cached_at": time.time(), "result": result.record()}
    return result


def gs1_import(path: Path | None) -> dict[str, dict]:
    """Import user-supplied GS1 bulk results as evidence; no API claims are made."""
    if not path: return {}
    if path.suffix.casefold() == ".xlsx":
        try:
            from openpyxl import load_workbook # pyright: ignore[reportMissingModuleSource]
        except ImportError as e:
            raise ValueError("Reading GS1 .xlsx imports requires openpyxl; export the sheet to CSV or install openpyxl") from e
        ws = load_workbook(path, read_only=True, data_only=True).active
        raw = list(ws.values)
        headers = [str(x or "").strip().casefold() for x in raw[0]]
        data = [dict(zip(headers, row)) for row in raw[1:]]
    else:
        with path.open(newline="", encoding="utf-8-sig") as f:
            data = list(csv.DictReader(f))
    output = {}
    for r in data:
        r = {str(k).strip().casefold(): ("" if v is None else str(v).strip()) for k, v in r.items()}
        code = r.get("barcode") or r.get("gtin") or r.get("gtin 14") or ""
        parsed = validate_gtin(code)
        if parsed.status != "VALID_CHECKSUM": continue
        status = r.get("status") or r.get("validation status") or "GS1_IMPORTED_RESULT"
        output[parsed.normalized] = {"source": "GS1_BULK_IMPORT", "status": status,
            "product_name": r.get("product_name") or r.get("product name") or r.get("description") or "",
            "brand": r.get("brand") or "", "quantity": r.get("quantity") or "",
            "company": r.get("licensed company") or r.get("licensee") or r.get("company") or "",
            "manufacturer": r.get("manufacturer") or "", "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": "Imported from user-provided GS1 bulk file; verify column meaning", "raw": r}
    return output


def process(path: Path, outdir: Path, sources: list[str], delay: float, resume: bool,
            verbose: bool = False, gs1_file: Path | None = None, offline: bool = False):
    if verbose: LOG.setLevel(logging.DEBUG)
    fields, rows = read_rows(path)
    providers_by_name = {"OpenFoodFacts": OpenFoodFactsProvider, "OpenBeautyFacts": OpenBeautyFactsProvider,
                         "OpenProductsFacts": OpenProductsFactsProvider}
    unknown = set(sources) - set(providers_by_name)
    if unknown: raise ValueError(f"Unsupported data source(s): {', '.join(sorted(unknown))}")
    providers = [providers_by_name[name](delay=delay) for name in sources]
    outdir.mkdir(parents=True, exist_ok=True)
    cache_path = outdir / ".barcode_audit_cache.json"
    cache = json_load(cache_path, {})
    gs1 = gs1_import(gs1_file)
    run_results = {}

    parsed = [validate_gtin(r.get("barcode", "")) for r in rows]
    by_code = defaultdict(list)
    for idx, gtin in enumerate(parsed):
        identity_key = gtin.normalized if gtin.status == "VALID_CHECKSUM" else gtin.raw
        if identity_key: by_code[identity_key].append(idx)

    product_results = []
    evidence_rows = []
    for i, (row, gtin) in enumerate(zip(rows, parsed), 1):
        name, expected_brand = (row.get("name") or "").strip(), (row.get("brand") or "").strip()
        expected_parent = (row.get("parent_company") or "").strip()
        pack = (row.get("packSize") or "").strip()
        identity_key = gtin.normalized if gtin.status == "VALID_CHECKSUM" else gtin.raw
        row_findings, all_evidence, found = [], [], []
        if gtin.status != "VALID_CHECKSUM":
            row_findings.append(gtin.status)
        if not name: row_findings.append("NEEDS_REVIEW: missing product name")
        if not expected_parent: row_findings.append("NEEDS_REVIEW: missing expected parent company")
        if len(by_code.get(identity_key, [])) > 1:
            row_findings.append("DUPLICATE_GTIN")
            cluster = [rows[j] for j in by_code[identity_key]]
            names = {" ".join((r.get("name") or "").casefold().split()) for r in cluster}
            brands = {" ".join((r.get("brand") or "").casefold().split()) for r in cluster}
            parents = {" ".join((r.get("parent_company") or "").casefold().split()) for r in cluster}
            if len(names) > 1 or len(brands) > 1 or len(parents) > 1:
                row_findings.append("DUPLICATE_GTIN: barcode is assigned to differing product, brand, or parent-company values")

        if gtin.status == "VALID_CHECKSUM" and not offline:
            # One lookup per canonical GTIN per selected source during this run.
            for provider in providers:
                lookup_key = f"{provider.source}:{identity_key}"
                if lookup_key not in run_results:
                    run_results[lookup_key] = cached_lookup(gtin.raw, provider, cache, resume, identity_key)
                    # Persist each completed response so --resume can continue
                    # after interruption, including negative results and errors.
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                result = run_results[lookup_key]
                rec = result.record(); rec["barcode"] = row.get("barcode", ""); rec["gtin_canonical"] = gtin.normalized
                all_evidence.append(rec); evidence_rows.append(rec)
                if result.status == "FOUND": found.append(result)
                LOG.debug("%s %s %s", provider.source, gtin.raw, result.status)
        if gtin.status == "VALID_CHECKSUM" and identity_key in gs1:
            g = gs1[identity_key]
            rec = dict(g, barcode=row.get("barcode", ""), gtin_canonical=identity_key)
            all_evidence.append(rec); evidence_rows.append(rec)
            if g["status"].upper() not in {"NOT_FOUND", "NOT FOUND", "INVALID", "NOT_REGISTERED"}:
                # imported GS1 identity evidence remains explicitly identified as bulk import
                found.append(ProviderResult("GS1_BULK_IMPORT", "FOUND", g["product_name"], g["brand"], g["quantity"], g["company"], g["retrieved_at"], g["error"], g["raw"]))

        comparisons = []
        for result in found:
            comp = compare_product(name, expected_brand, pack, result.product_name, result.brand, result.quantity)
            manufacturer = str((result.raw or {}).get("manufacturer") or "")
            company_match = compare_company(expected_parent, result.company) if result.company else "UNKNOWN"
            comparisons.append((result, comp, company_match, manufacturer))
            if comp.identity == "MISMATCH": row_findings.append("POSSIBLE_MISMATCH")
            if comp.brand == "MISMATCH": row_findings.append("BRAND_MISMATCH")
            if company_match == "POSSIBLE_MISMATCH":
                row_findings.append("PARENT_COMPANY_MISMATCH: external brand owner differs by name; ownership relationship is not established")
            if comp.quantity == "MISMATCH": row_findings.append("QUANTITY_MISMATCH")
        identity_outcomes = [c[1].identity for c in comparisons]
        if len(identity_outcomes) >= 2 and all(x == "MISMATCH" for x in identity_outcomes):
            row_findings = [x for x in row_findings if x != "POSSIBLE_MISMATCH"] + ["CONFIRMED_MISMATCH"]
        elif not found and not offline and gtin.status == "VALID_CHECKSUM":
            statuses = [r["status"] for r in all_evidence]
            row_findings.append("NOT_FOUND" if statuses and all(s == "NOT_FOUND_IN_SOURCE" for s in statuses) else "UNVERIFIED")
        elif offline and gtin.status == "VALID_CHECKSUM":
            row_findings.append("UNVERIFIED")
        elif found and all(c[1].identity == "MATCH" for c in comparisons):
            row_findings.append("MATCH")
        elif found and any(c[1].identity == "POSSIBLE_MATCH" for c in comparisons):
            row_findings.append("NEEDS_REVIEW")

        primary = comparisons[0] if comparisons else None
        first = primary[0] if primary else None
        comp = primary[1] if primary else None
        c_match = primary[2] if primary else "UNKNOWN"
        mfr = primary[3] if primary else ""
        direct_found = bool(found)
        confidence = "LOW"
        if len(comparisons) >= 2 and len({x[1].identity for x in comparisons}) == 1: confidence = "MODERATE"
        elif comparisons and comp.identity == "MATCH" and comp.brand == "MATCH": confidence = "MODERATE"
        if any(r.source == "GS1_BULK_IMPORT" for r in found): confidence = "HIGH"
        findings = list(dict.fromkeys(x.split(":", 1)[0] for x in row_findings))
        if not findings: findings = ["UNVERIFIED"]
        status_order = ["INVALID_FORMAT", "INVALID_CHECK_DIGIT", "DUPLICATE_GTIN", "CONFIRMED_MISMATCH", "POSSIBLE_MISMATCH", "BRAND_MISMATCH", "PARENT_COMPANY_MISMATCH", "QUANTITY_MISMATCH", "NOT_FOUND", "UNVERIFIED", "NEEDS_REVIEW", "MATCH"]
        verification_status = next((s for s in status_order if s in findings), "NEEDS_REVIEW")
        reason = "; ".join(dict.fromkeys(row_findings + ([reason for _, c, _, _ in comparisons for reason in c.reasons])))
        evidence_summary = json.dumps(all_evidence, ensure_ascii=False, separators=(",", ":"))
        rec = dict(row)
        rec.update({"barcode_status": gtin.status, "product_match_score": comp.match_score if comp else "",
            "brand_match": comp.brand if comp else "UNKNOWN", "parent_company_match": c_match,
            "quantity_match": comp.quantity if comp else "UNKNOWN", "verification_status": verification_status,
            "confidence": confidence, "mismatch_reason": reason, "sources_checked": ";".join(sources) if not offline else "",
            "sources_found": ";".join(r.source for r in found), "evidence": evidence_summary,
            "findings": ";".join(findings), "external_product_name": first.product_name if first else "",
            "external_brand": first.brand if first else "", "external_manufacturer": mfr,
            "external_brand_owner": first.company if first else "", "external_quantity": first.quantity if first else "",
            "source_database": first.source if first else "", "recommended_investigation": recommendation(findings, expected_parent, bool(found))})
        product_results.append(rec)
        print(f"[{i}/{len(rows)}] {verification_status:<24} {name[:48]}")

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(outdir / "results.csv", fields + [f for f in OUTPUT_FIELDS if f not in fields], product_results)
    flagged = [r for r in product_results if r["verification_status"] != "MATCH"]
    write_csv(outdir / "flagged_products.csv", fields + [f for f in OUTPUT_FIELDS if f not in fields], flagged)
    write_csv(outdir / "source_evidence.csv", ["barcode", "gtin_canonical", "source", "status", "product_name", "brand", "quantity", "company", "retrieved_at", "error", "raw"], evidence_rows)
    summary(product_results, sources, outdir / "audit_summary.txt", offline)
    return product_results


def recommendation(findings: list[str], expected_parent: str, found: bool) -> str:
    if "INVALID_FORMAT" in findings or "INVALID_CHECK_DIGIT" in findings: return "Check the source barcode transcription and scan the physical package."
    if "DUPLICATE_GTIN" in findings: return "Review every row sharing this GTIN and verify against package images or supplier records."
    if "CONFIRMED_MISMATCH" in findings or "POSSIBLE_MISMATCH" in findings: return "Compare the package, product variant, and barcode with the supplier; database evidence alone does not prove fraud."
    if "PARENT_COMPANY_MISMATCH" in findings: return "Verify brand ownership or licensee using authoritative company/GS1 records; manufacturer and owner may differ."
    if "BRAND_MISMATCH" in findings: return "Check the label for brand, licensed brand, or product-line differences."
    if "QUANTITY_MISMATCH" in findings: return "Check whether the GTIN identifies a different pack size or market variant."
    if "NOT_FOUND" in findings: return "Look up through authorized GS1 services or confirm from package and supplier documentation."
    if not found: return "No source evidence was available; verify from package, supplier, or authorized GS1 data."
    return "Review the source fields and package if higher assurance is needed."


def write_csv(path: Path, fields: list[str], rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def summary(rows: list[dict], sources: list[str], path: Path, offline: bool):
    counts = Counter(r["verification_status"] for r in rows)
    findings = Counter(f for r in rows for f in (r.get("findings", "").split(";") if r.get("findings") else []))
    lines = ["Barcode and Product Identity Audit", f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             f"Products audited: {len(rows)}", f"Sources selected: {', '.join(sources) if sources else 'none'}",
             f"External lookup performed: {'no (offline audit)' if offline else 'yes, subject to cache/provider availability'}",
             "GS1 live API: disabled (requires authorized documentation and credentials)", "", "Primary classifications:"]
    lines += [f"  {s}: {counts.get(s, 0)}" for s in ["CONFIRMED_MISMATCH", "POSSIBLE_MISMATCH", "BRAND_MISMATCH", "PARENT_COMPANY_MISMATCH", "QUANTITY_MISMATCH", "DUPLICATE_GTIN", "INVALID_FORMAT", "INVALID_CHECK_DIGIT", "NOT_FOUND", "UNVERIFIED", "MATCH", "NEEDS_REVIEW"]]
    lines += ["", "All findings (may overlap):"] + [f"  {k}: {v}" for k, v in sorted(findings.items())]
    lines += ["", "A valid check digit confirms checksum structure only. Database absence is not proof of invalidity.",
              "Manufacturer, distributor, importer, brand owner, and GS1 licensee can be different entities."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--sources", default="OpenFoodFacts,OpenBeautyFacts,OpenProductsFacts", help="Comma-separated provider names")
    parser.add_argument("--delay", type=float, default=.7)
    parser.add_argument("--resume", action="store_true", help="Reuse cached provider responses within their expiry windows")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Run local GTIN/duplicate checks without external lookups")
    parser.add_argument("--gs1-import", type=Path, help="Merge authorized GS1 bulk results from CSV or XLSX")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    try:
        results = process(args.input_csv, args.output_dir, sources, args.delay, args.resume, args.verbose, args.gs1_import, args.offline)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    counts = Counter(r["verification_status"] for r in results)
    print(f"\nSaved results.csv, flagged_products.csv, source_evidence.csv, and audit_summary.txt to {args.output_dir}")
    print("; ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
