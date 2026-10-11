"""Guardrails for LLM text: every number in a generated answer must be traceable to the bill or to code-computed results."""
from __future__ import annotations
import re

_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")  # Urdu/Arabic-Indic -> Western


def numbers_in(text: str) -> list[float]:
    out = []
    for raw in _NUM.findall(str(text).translate(_DIGITS)):
        try:
            out.append(abs(float(raw.replace(",", ""))))
        except ValueError:
            pass
    return out


def _ignorable(x: float) -> bool:
    """Calendar-ish numbers the model may freely use: day-of-month / small counts, and years."""
    return (x == int(x) and x <= 31) or (x == int(x) and 1900 <= x <= 2100)


def close(x: float, a: float) -> bool:
    return abs(x - a) <= max(0.51, 0.005 * a)


def unsupported(text: str, allowed: list[float]) -> list[float]:
    """Numbers in `text` that match nothing in `allowed` (rounding tolerant)."""
    return [x for x in numbers_in(text) if not _ignorable(x) and not any(close(x, a) for a in allowed)]


def translation_ok(src: str, dst: str) -> bool:
    """A translation must keep every number and add none."""
    s, d = numbers_in(src), numbers_in(dst)
    missing = [x for x in s if not any(close(x, y) for y in d)]
    return not missing and not unsupported(dst, s)
