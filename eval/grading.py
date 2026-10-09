"""Deterministic grading: numbers parsed from the answer, or expected strings from golden SQL."""
import re

_NUM = re.compile(r"(-?)\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m|bn|b|million|thousand|billion)?\b", re.I)
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


def extract_numbers(text: str) -> list[float]:
    out = []
    for sign, num, suffix in _NUM.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        v *= _MULT.get(suffix.lower(), 1) if suffix else 1
        out.append(-v if sign else v)
    return out


def number_matches(answer: str, expected: float, tol: float = 0.02, absolute: bool = False) -> bool:
    expected = abs(expected) if absolute else expected
    for v in extract_numbers(answer):
        v = abs(v) if absolute else v
        if expected == 0:
            if abs(v) < 1e-9:
                return True
        elif abs(v - expected) / abs(expected) <= tol:
            return True
    return False


def grade(q: dict, answer: str, db) -> tuple[bool, str]:
    """Return (passed, reason). Every check present on the question must pass."""
    a = answer.lower()
    if not answer.strip():
        return False, "empty_answer"
    if "number_sql" in q:
        exp = float(db.run(q["number_sql"]).rows[0][0])
        if not number_matches(answer, exp, q.get("tol", 0.02), q.get("abs", False)):
            return False, f"wrong_number (expected ~{exp:.4g})"
    if "any_of_sql" in q:
        row = db.run(q["any_of_sql"]).rows[0]
        if not any(str(v).lower() in a for v in row):
            return False, f"missing_expected_text (expected one of {list(row)})"
    for group in q.get("contains_all", []):
        if not any(alt in a for alt in group):
            return False, f"missing_concept {group}"
    if "contains_any" in q and not any(s in a for s in q["contains_any"]):
        return False, "missing_expected_phrase (trap not recognised)"
    return True, "ok"
