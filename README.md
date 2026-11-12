# Barcode and Product Identity Audit

Run from this directory:

```sh
python3 -m pip install -r requirements.txt
python3 check_barcodes.py products_with_parent.csv
```

The command writes `results.csv`, `flagged_products.csv`, `source_evidence.csv`,
`audit_summary.txt`, and a resumable response cache to the chosen output
directory. The input CSV is read without modifying it. `--resume` reuses cached
provider responses (30 days for found products, one day for missing products,
five minutes for errors).

Useful options:

```sh
python3 check_barcodes.py products_with_parent.csv --offline
python3 check_barcodes.py products_with_parent.csv --output-dir audit --resume
python3 check_barcodes.py products_with_parent.csv --sources OpenFoodFacts --delay 1
python3 check_barcodes.py products_with_parent.csv --gs1-import authorized_gs1_export.xlsx
```

Supported public provider names are `OpenFoodFacts`, `OpenBeautyFacts`, and
`OpenProductsFacts`. `--offline` performs local GTIN and duplicate checks and
marks valid codes unverified because it makes no external claims. A valid
checksum does not establish GS1 registration. The GS1 API provider is disabled:
the project needs authorized service documentation and credentials before a
live integration can be implemented. Authorized GS1 CSV/XLSX bulk results can
be imported as explicitly labeled evidence.

Run the offline regression suite with:

```sh
python3 -m unittest discover -s tests -v
```

The Open Facts providers currently retain the v2 product lookup requested by
the original checker. The Open Food Facts documentation labels v2 a legacy API;
provider modules isolate that endpoint for a future documented migration.
