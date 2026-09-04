"""Active-symbol discovery and semantic name resolution.

Instruments are identified by their display name (configured in
config/settings.toml). Symbol IDs such as ``1HZ75V`` are discovered at
runtime from the current ``active_symbols`` response and must never be
hard-coded as authoritative.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from smb.deriv.client import DerivClient

logger = logging.getLogger(__name__)


class SymbolNotFoundError(LookupError):
    """No active symbol matched the requested name."""


class SymbolAmbiguousError(LookupError):
    """More than one active symbol matched the requested name."""


@dataclass(frozen=True)
class SymbolInfo:
    """Normalized representation of a Deriv instrument.

    Fields mirror the current official active_symbols response
    (https://developers.deriv.com/llms/active-symbols.md).
    """

    symbol: str  # underlying_symbol
    name: str  # underlying_symbol_name
    market: str | None = None
    submarket: str | None = None
    subgroup: str | None = None
    pip_size: float | None = None
    underlying_symbol_type: str | None = None
    exchange_is_open: bool | None = None
    is_trading_suspended: bool | None = None
    trade_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> SymbolInfo:
        """Build SymbolInfo from a single active_symbols entry."""
        return cls(
            symbol=str(item["underlying_symbol"]),
            name=str(item["underlying_symbol_name"]),
            market=item.get("market"),
            submarket=item.get("submarket"),
            subgroup=item.get("subgroup"),
            pip_size=_as_float(item.get("pip_size")),
            underlying_symbol_type=item.get("underlying_symbol_type") or None,
            exchange_is_open=_as_bool(item.get("exchange_is_open")),
            is_trading_suspended=_as_bool(item.get("is_trading_suspended")),
            trade_count=_as_int(item.get("trade_count")),
            raw=dict(item),
        )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def _normalize_name(name: str) -> str:
    """Collapse whitespace and lower-case for tolerant matching."""
    return re.sub(r"\s+", " ", name.strip()).lower()


def normalize_symbol(item: dict[str, Any]) -> SymbolInfo:
    """Public helper: convert a raw API dict into SymbolInfo."""
    return SymbolInfo.from_api(item)


def resolve_symbol(
    name: str,
    active_symbols: Sequence[dict[str, Any] | SymbolInfo],
) -> SymbolInfo:
    """Resolve a semantic instrument name against active_symbols data.

    Matching is case- and whitespace-insensitive on the display name
    (``underlying_symbol_name``). Exact normalized equality is required;
    partial / fuzzy matches are rejected.

    Raises:
        SymbolNotFoundError: zero matches.
        SymbolAmbiguousError: two or more matches.
    """
    target = _normalize_name(name)
    if not target:
        raise SymbolNotFoundError("Empty instrument name")

    matches: list[SymbolInfo] = []
    for item in active_symbols:
        if isinstance(item, SymbolInfo):
            info = item
        else:
            try:
                info = SymbolInfo.from_api(item)
            except (KeyError, TypeError) as exc:
                logger.debug("Skipping malformed symbol entry: %s", exc)
                continue
        if _normalize_name(info.name) == target:
            matches.append(info)

    if not matches:
        raise SymbolNotFoundError(
            f"No active symbol found for name {name!r}. "
            "Check config/settings.toml and the current active_symbols response."
        )
    if len(matches) > 1:
        ids = ", ".join(m.symbol for m in matches)
        raise SymbolAmbiguousError(
            f"Ambiguous name {name!r} matched multiple symbols: {ids}. "
            "Use a more specific configured name."
        )
    return matches[0]


async def load_active_symbols(
    client: DerivClient,
    *,
    detail: str = "full",
) -> list[SymbolInfo]:
    """Request active_symbols and return a list of normalized SymbolInfo."""
    if detail not in ("full", "brief"):
        raise ValueError("detail must be 'full' or 'brief'")

    response = await client.request({"active_symbols": detail})
    raw_list = response.get("active_symbols")
    if not isinstance(raw_list, list):
        raise ValueError(
            f"Unexpected active_symbols response shape: missing or non-list "
            f"'active_symbols' (msg_type={response.get('msg_type')!r})"
        )

    symbols: list[SymbolInfo] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        try:
            symbols.append(SymbolInfo.from_api(item))
        except (KeyError, TypeError) as exc:
            logger.warning("Skipping malformed active_symbols entry: %s", exc)
    return symbols
