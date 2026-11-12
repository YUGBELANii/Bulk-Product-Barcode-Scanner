"""Conservative product identity and quantity comparison."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

STOP = {"the", "of", "and", "with", "for", "pack", "piece", "pc", "free", "get", "buy", "bottle"}
UNIT = {"g": "mass", "gm": "mass", "gram": "mass", "grams": "mass", "kg": "mass",
        "ml": "volume", "l": "volume", "litre": "volume", "liter": "volume", "litres": "volume", "liters": "volume",
        "mg": "mass", "oz": "mass"}


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").casefold()))


def tokens(text: str) -> set[str]:
    return {w for w in normalize(text).split() if w not in STOP and len(w) > 1}


def similarity(a: str, b: str) -> tuple[float, float]:
    ta, tb = tokens(a), tokens(b)
    token_score = len(ta & tb) / max(1, len(ta | tb))
    fuzzy = SequenceMatcher(None, normalize(a), normalize(b)).ratio()
    return round(fuzzy * 100, 1), round(token_score * 100, 1)


def brand_match(expected: str, actual: str) -> str:
    exp = {normalize(x) for x in re.split(r"[/,;|]", expected or "") if normalize(x)}
    act = {normalize(x) for x in re.split(r"[/,;|]", actual or "") if normalize(x)}
    if not exp or not act:
        return "UNKNOWN"
    if exp & act:
        return "MATCH"
    et = set().union(*(tokens(x) for x in exp)); at = set().union(*(tokens(x) for x in act))
    if et & at and (et <= at or at <= et):
        return "POSSIBLE_MATCH"
    return "MISMATCH"


def quantity(value: str) -> tuple[float, str] | None:
    m = re.search(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(kg|g|gm|grams?|ml|l|lit(?:er|re)s?|mg|oz)\b", (value or "").casefold())
    if not m: return None
    n = float(m.group(1).replace(",", ".")); unit = m.group(2)
    kind = UNIT[unit]
    if unit == "kg": n *= 1000
    elif unit in {"l", "litre", "liter", "litres", "liters"}: n *= 1000
    return n, kind


def quantity_match(expected: str, actual: str) -> str:
    a, b = quantity(expected), quantity(actual)
    if not a or not b: return "UNKNOWN"
    return "MATCH" if a == b else "MISMATCH"


@dataclass
class ProductComparison:
    match_score: float | str
    name_fuzzy_score: float | str
    token_score: float | str
    identity: str
    brand: str
    quantity: str
    reasons: list[str]


def compare_product(expected_name: str, expected_brand: str, expected_qty: str,
                    external_name: str, external_brand: str, external_qty: str) -> ProductComparison:
    if not external_name and not external_brand:
        return ProductComparison("", "", "", "UNKNOWN", "UNKNOWN", "UNKNOWN", ["External record has no identifying product fields"])
    fuzzy, tok = similarity(expected_name, external_name)
    bm = brand_match(expected_brand, external_brand)
    qm = quantity_match(expected_qty, external_qty)
    a, b = tokens(expected_name), tokens(external_name)
    # Brand conflict is strong negative evidence; product-specific token disagreement
    # also blocks a positive verdict despite generic category-word overlap.
    identity = "POSSIBLE_MATCH"
    reasons = []
    if bm == "MISMATCH":
        identity = "MISMATCH"; reasons.append("Expected and external brands differ")
    elif len(a & b) == 0 or (tok < 25 and fuzzy < 45):
        identity = "MISMATCH"; reasons.append("Product name evidence is weak or disjoint")
    elif tok >= 35 and fuzzy >= 55 and bm in {"MATCH", "POSSIBLE_MATCH"}:
        identity = "MATCH"; reasons.append("Name and brand evidence align")
    else:
        reasons.append("Name or brand evidence is incomplete; review recommended")
    if qm == "MISMATCH": reasons.append("Quantity differs; pack size mismatch does not alone establish a different product")
    return ProductComparison(round((fuzzy + tok) / 2, 1), fuzzy, tok, identity, bm, qm, reasons)
