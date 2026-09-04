"""Deriv API client, symbol discovery, and historical ticks."""

from smb.deriv.client import DerivAPIError, DerivClient
from smb.deriv.history import (
    MAX_TICKS_PER_REQUEST,
    HistoryPage,
    Tick,
    TickStats,
    compute_tick_stats,
    fetch_ticks,
    fetch_ticks_paginated,
    flatten_pages,
    parse_history_response,
)
from smb.deriv.symbols import (
    SymbolAmbiguousError,
    SymbolInfo,
    SymbolNotFoundError,
    load_active_symbols,
    normalize_symbol,
    resolve_symbol,
)

__all__ = [
    "DerivAPIError",
    "DerivClient",
    "SymbolInfo",
    "SymbolNotFoundError",
    "SymbolAmbiguousError",
    "normalize_symbol",
    "resolve_symbol",
    "load_active_symbols",
    "Tick",
    "HistoryPage",
    "TickStats",
    "MAX_TICKS_PER_REQUEST",
    "parse_history_response",
    "fetch_ticks",
    "fetch_ticks_paginated",
    "flatten_pages",
    "compute_tick_stats",
]
