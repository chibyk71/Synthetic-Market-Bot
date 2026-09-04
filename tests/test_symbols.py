"""Unit tests for symbol discovery and resolution (no network)."""

from __future__ import annotations

import pytest

from smb.deriv.symbols import (
    SymbolAmbiguousError,
    SymbolInfo,
    SymbolNotFoundError,
    normalize_symbol,
    resolve_symbol,
)

# Minimal realistic active_symbols payload derived from the current
# official response shape (underlying_symbol / underlying_symbol_name / ...).
SAMPLE_ACTIVE_SYMBOLS = [
    {
        "exchange_is_open": 1,
        "is_trading_suspended": 0,
        "market": "synthetic_index",
        "pip_size": 0.01,
        "subgroup": "synthetics",
        "submarket": "random_index",
        "trade_count": 2143617,
        "underlying_symbol": "1HZ75V",
        "underlying_symbol_name": "Volatility 75 (1s) Index",
        "underlying_symbol_type": "stockindex",
    },
    {
        "exchange_is_open": 1,
        "is_trading_suspended": 0,
        "market": "synthetic_index",
        "pip_size": 0.1,
        "subgroup": "synthetics",
        "submarket": "step_index",
        "trade_count": 198444,
        "underlying_symbol": "stpRNG",
        "underlying_symbol_name": "Step Index 100",
        "underlying_symbol_type": "",
    },
    {
        "exchange_is_open": 1,
        "is_trading_suspended": 0,
        "market": "synthetic_index",
        "pip_size": 0.01,
        "subgroup": "synthetics",
        "submarket": "random_index",
        "trade_count": 100,
        "underlying_symbol": "1HZ100V",
        "underlying_symbol_name": "Volatility 100 (1s) Index",
        "underlying_symbol_type": "stockindex",
    },
    {
        "exchange_is_open": 1,
        "is_trading_suspended": 0,
        "market": "synthetic_index",
        "pip_size": 0.1,
        "subgroup": "synthetics",
        "submarket": "step_index",
        "trade_count": 50,
        "underlying_symbol": "stpRNG2",
        "underlying_symbol_name": "Step Index 200",
        "underlying_symbol_type": "",
    },
]


def test_exact_match_volatility_75_1s():
    info = resolve_symbol("Volatility 75 (1s) Index", SAMPLE_ACTIVE_SYMBOLS)
    assert info.symbol == "1HZ75V"
    assert info.name == "Volatility 75 (1s) Index"
    assert info.market == "synthetic_index"
    assert info.submarket == "random_index"
    assert info.pip_size == 0.01


def test_exact_match_step_index():
    info = resolve_symbol("Step Index 100", SAMPLE_ACTIVE_SYMBOLS)
    assert info.symbol == "stpRNG"
    assert info.name == "Step Index 100"
    assert info.submarket == "step_index"
    assert info.pip_size == 0.1


def test_case_and_whitespace_tolerance():
    info = resolve_symbol("  volatility  75  (1s)  index  ", SAMPLE_ACTIVE_SYMBOLS)
    assert info.symbol == "1HZ75V"

    info2 = resolve_symbol("STEP INDEX 100", SAMPLE_ACTIVE_SYMBOLS)
    assert info2.symbol == "stpRNG"


def test_missing_instrument():
    with pytest.raises(SymbolNotFoundError) as exc_info:
        resolve_symbol("Nonexistent Index XYZ", SAMPLE_ACTIVE_SYMBOLS)
    assert "Nonexistent Index XYZ" in str(exc_info.value)


def test_empty_name():
    with pytest.raises(SymbolNotFoundError):
        resolve_symbol("   ", SAMPLE_ACTIVE_SYMBOLS)


def test_ambiguous_instrument():
    # Duplicate the volatility entry under the same display name.
    ambiguous = list(SAMPLE_ACTIVE_SYMBOLS) + [
        {
            **SAMPLE_ACTIVE_SYMBOLS[0],
            "underlying_symbol": "1HZ75V_DUP",
        }
    ]
    with pytest.raises(SymbolAmbiguousError) as exc_info:
        resolve_symbol("Volatility 75 (1s) Index", ambiguous)
    msg = str(exc_info.value)
    assert "1HZ75V" in msg
    assert "1HZ75V_DUP" in msg


def test_normalize_symbol_metadata():
    raw = SAMPLE_ACTIVE_SYMBOLS[0]
    info = normalize_symbol(raw)
    assert isinstance(info, SymbolInfo)
    assert info.symbol == "1HZ75V"
    assert info.name == "Volatility 75 (1s) Index"
    assert info.pip_size == 0.01
    assert info.exchange_is_open is True
    assert info.is_trading_suspended is False
    assert info.trade_count == 2143617
    assert info.raw["underlying_symbol"] == "1HZ75V"


def test_resolve_accepts_symbol_info_objects():
    infos = [normalize_symbol(s) for s in SAMPLE_ACTIVE_SYMBOLS]
    info = resolve_symbol("Volatility 75 (1s) Index", infos)
    assert info.symbol == "1HZ75V"


def test_partial_name_does_not_match():
    """A shorter substring must not silently select an instrument."""
    with pytest.raises(SymbolNotFoundError):
        resolve_symbol("Volatility 75", SAMPLE_ACTIVE_SYMBOLS)
    with pytest.raises(SymbolNotFoundError):
        resolve_symbol("Step Index", SAMPLE_ACTIVE_SYMBOLS)
