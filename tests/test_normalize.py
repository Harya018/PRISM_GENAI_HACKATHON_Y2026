"""Self-authored unit tests for agent/normalize.py (A1) -- no benchmark data, no scenario ids."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from normalize import normalize_args, _coerce_number, _coerce_currency, _words_to_number


def test_spoken_number_coerced():
    assert _words_to_number("eighteen hundred") == 1800.0
    assert _words_to_number("twenty-five") == 25.0
    assert _words_to_number("one thousand two hundred") == 1200.0
    assert _words_to_number("nine") == 9.0


def test_spoken_number_unrecognized_returns_none():
    assert _words_to_number("a lot") is None
    assert _words_to_number("") is None
    assert _words_to_number("1800") is None  # digits, not words -- handled by _coerce_number instead


def test_coerce_number_already_valid_untouched():
    assert _coerce_number(1800) is None
    assert _coerce_number(1800.5) is None


def test_coerce_number_bool_never_touched():
    # bool is a subclass of int in Python -- must never be treated as a coercion target
    assert _coerce_number(True) is None
    assert _coerce_number(False) is None


def test_coerce_number_formatted_string():
    assert _coerce_number("$1,800.00") == 1800
    assert _coerce_number("1,800") == 1800
    assert _coerce_number("99.99") == 99.99


def test_coerce_number_spoken_string():
    assert _coerce_number("eighteen hundred") == 1800
    assert _coerce_number("three") == 3


def test_coerce_number_unparseable_returns_none():
    assert _coerce_number("a couple") is None
    assert _coerce_number("downtown") is None


def test_coerce_currency_already_valid_untouched():
    assert _coerce_currency("USD") is None
    assert _coerce_currency("eur") is None  # valid code, case-insensitive check -- still untouched


def test_coerce_currency_name_mapped():
    assert _coerce_currency("dollars") == "USD"
    assert _coerce_currency("Euros") == "EUR"
    assert _coerce_currency("pounds") == "GBP"


def test_coerce_currency_unrecognized_returns_none():
    assert _coerce_currency("bitcoin") is None
    assert _coerce_currency("XYZ") is None  # 3 letters but not a known ISO code -- never guess


def test_normalize_args_exchange_rate_full():
    out = normalize_args("get_exchange_rate",
                         {"amount": "eighteen hundred", "from_currency": "dollars", "to_currency": "EUR"})
    assert out == {"amount": 1800, "from_currency": "USD", "to_currency": "EUR"}


def test_normalize_args_already_valid_passes_through_unchanged():
    args = {"amount": 500.0, "from_currency": "USD", "to_currency": "EUR"}
    out = normalize_args("get_exchange_rate", args)
    assert out == args
    assert out is not args  # always a new dict, but content identical


def test_normalize_args_non_numeric_tool_untouched():
    args = {"destination": "Paris", "date": "next Friday"}
    out = normalize_args("search_flights", args)
    assert out == args


def test_normalize_args_search_apartments_bedrooms_and_price():
    out = normalize_args("search_apartments",
                         {"city": "Atlanta", "bedrooms": "three", "max_price": "$1,800"})
    assert out == {"city": "Atlanta", "bedrooms": 3, "max_price": 1800}


def test_normalize_args_add_to_cart_quantity():
    out = normalize_args("add_to_cart", {"product_id": "P52", "quantity": "two"})
    assert out == {"product_id": "P52", "quantity": 2}


def test_normalize_args_unparseable_left_alone():
    # a genuinely ambiguous value must be left as-is, not guessed -- downstream (the mock) will
    # then fail loudly on it rather than silently executing with a wrong number
    out = normalize_args("add_to_cart", {"product_id": "P52", "quantity": "a couple"})
    assert out["quantity"] == "a couple"
