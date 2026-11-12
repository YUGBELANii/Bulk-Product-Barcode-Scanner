"""Conservative expected-versus-reported company comparison."""
from comparison import normalize, tokens


def compare_company(expected: str, reported: str) -> str:
    if not expected or not reported: return "UNKNOWN"
    a, b = normalize(expected), normalize(reported)
    if a == b: return "MATCH"
    # Only transparent lexical equivalence is inferred; no ownership graph is invented.
    at, bt = tokens(a), tokens(b)
    if at and bt and (at <= bt or bt <= at): return "POSSIBLE_MATCH"
    return "POSSIBLE_MISMATCH"
