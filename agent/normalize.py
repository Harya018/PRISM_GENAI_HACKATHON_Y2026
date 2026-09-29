"""Schema-driven argument normalization (A1), applied in the commit gate right before execution.

Read against `agent/lk_agent.py`'s own `TOOL_SCHEMAS` (declared arg types) and
`Full-Duplex-Bench/v3/mock_apis.py`'s own function bodies (not benchmark ground truth) to learn
what each tool actually accepts or rejects:

- Every numeric arg (`get_exchange_rate.amount`, `search_apartments.bedrooms`/`max_price`,
  `search_products.max_price`, `add_to_cart.quantity`) is used in arithmetic by the mock
  (`max_price - 100`, `99.99 * quantity`, ...) -- passing a string there is invalid: it raises
  a TypeError, a real crash the mock does NOT tolerate. Spoken ("eighteen hundred") or formatted
  ("$1,800.00") numbers are exactly what a realtime voice model can plausibly emit here.
- `get_exchange_rate.from_currency`/`to_currency` are read as plain strings by the mock (no
  crash either way), but the tool's own contract is an ISO 4217 code -- normalizing a spoken
  currency name to its code doesn't change mock behavior but does match what the tool schema
  documents as the accepted format.
- No other declared arg (destination/date/doc_type/doc_number/card_type/bill_type/
  source_account/city/addresses/filter_name/order_id/query/product_id/passenger_name) is used
  in a way the mock could reject regardless of what string it receives -- there is nothing to
  normalize for those, so nothing here touches them. Adding a normalizer with no corresponding
  rejection case in the mock would be exactly the "not grounded in the tool implementation"
  problem this module exists to avoid.

Only transforms a value when it is NOT already valid for its declared type AND the transformed
value IS confidently valid -- an already-correct value is never touched, and an ambiguous one
(e.g. a spoken number this module can't confidently parse) is left alone rather than guessed.

Flag-gated: `LK_NORMALIZE_ARGS=1` (default off) wires this into `agent/commit_gate.py` via its
`normalize_fn` hook -- see `agent/lk_agent.py`. Self-tested only (`tests/test_normalize.py`);
never reads benchmark scenario data.
"""
from __future__ import annotations

import re
from typing import Any, Dict

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000}


def _words_to_number(text: str) -> float | None:
    """"eighteen hundred" -> 1800.0, "one thousand two hundred" -> 1200.0, "twenty-five" ->
    25.0. Returns None (never guesses) if any token isn't a recognized number word."""
    tokens = [t for t in text.lower().replace("-", " ").split() if t != "and"]
    if not tokens or not all(t in _ONES or t in _TENS or t in _SCALES for t in tokens):
        return None
    total, current = 0, 0
    for t in tokens:
        if t in _ONES:
            current += _ONES[t]
        elif t in _TENS:
            current += _TENS[t]
        elif t in _SCALES:
            scale = _SCALES[t]
            current = (current or 1) * scale
            if scale >= 1000:
                total += current
                current = 0
    total += current
    return float(total) if total > 0 else None


def _coerce_number(value: Any) -> float | int | None:
    """Returns a valid number for `value` if it wasn't already one, or None if it's already
    valid (nothing to do) or can't be confidently parsed (never guesses)."""
    if isinstance(value, bool):
        return None  # bool is a subclass of int -- never coerce/touch it as if it were numeric
    if isinstance(value, (int, float)):
        return None  # already valid
    if not isinstance(value, str):
        return None
    s = value.strip()
    cleaned = re.sub(r"[,$\s]", "", s)
    try:
        f = float(cleaned)
        return int(f) if f.is_integer() else f
    except ValueError:
        pass
    n = _words_to_number(s)
    if n is None:
        return None
    return int(n) if n.is_integer() else n


_CURRENCY_NAMES = {
    "dollar": "USD", "dollars": "USD", "us dollar": "USD", "us dollars": "USD",
    "euro": "EUR", "euros": "EUR",
    "pound": "GBP", "pounds": "GBP", "sterling": "GBP", "british pound": "GBP",
    "yen": "JPY",
    "rupee": "INR", "rupees": "INR",
}
_VALID_ISO_CODES = {"USD", "EUR", "GBP", "JPY", "INR", "CAD", "AUD", "CHF", "CNY"}


def _coerce_currency(value: Any) -> str | None:
    """Returns an ISO 4217 code if `value` wasn't already a recognized one, else None (nothing
    to do, or can't confidently map)."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if re.fullmatch(r"[A-Za-z]{3}", s) and s.upper() in _VALID_ISO_CODES:
        return None  # already valid -- don't touch it
    return _CURRENCY_NAMES.get(s.lower())


# tool_name -> set of arg names the mock uses in arithmetic (schema type "number")
NUMERIC_ARGS = {
    "get_exchange_rate": {"amount"},
    "search_apartments": {"bedrooms", "max_price"},
    "search_products": {"max_price"},
    "add_to_cart": {"quantity"},
}
# tool_name -> set of arg names whose declared format is an ISO 4217 currency code
CURRENCY_ARGS = {
    "get_exchange_rate": {"from_currency", "to_currency"},
}


def normalize_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a NEW dict: any value that isn't already valid for its declared type gets
    replaced by a confidently-normalized valid one; everything else (already valid, or
    ambiguous/unparseable) passes through completely untouched."""
    out = dict(args)
    for arg_name in NUMERIC_ARGS.get(tool_name, ()):
        if arg_name in out:
            coerced = _coerce_number(out[arg_name])
            if coerced is not None:
                out[arg_name] = coerced
    for arg_name in CURRENCY_ARGS.get(tool_name, ()):
        if arg_name in out:
            coerced = _coerce_currency(out[arg_name])
            if coerced is not None:
                out[arg_name] = coerced
    return out
