"""Deriv API client and symbol discovery."""

from smb.deriv.client import DerivAPIError, DerivClient
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
]
