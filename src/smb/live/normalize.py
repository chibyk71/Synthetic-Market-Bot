"""Normalize Deriv tick messages into LiveTick."""

from __future__ import annotations

from typing import Any

from smb.live.models import LiveTick


class MalformedTickError(ValueError):
    """Raised when a message cannot be converted into a valid LiveTick."""


def normalize_tick_message(
    message: dict[str, Any],
    *,
    instrument: str,
    expected_symbol: str | None = None,
) -> LiveTick:
    if not isinstance(message, dict):
        raise MalformedTickError(f"tick message must be a dict, got {type(message)}")
    body = message
    if "tick" in message and isinstance(message["tick"], dict):
        body = message["tick"]
    symbol = body.get("symbol") or body.get("underlying_symbol")
    if symbol is None or not str(symbol).strip():
        raise MalformedTickError("missing symbol")
    symbol = str(symbol).strip()
    if expected_symbol is not None and symbol != expected_symbol:
        raise MalformedTickError(
            f"symbol mismatch: got {symbol!r}, expected {expected_symbol!r}"
        )
    price = body.get("quote")
    if price is None:
        price = body.get("price")
    if price is None:
        raise MalformedTickError("missing price/quote")
    try:
        price_f = float(price)
    except (TypeError, ValueError) as exc:
        raise MalformedTickError(f"invalid price: {price!r}") from exc
    if price_f != price_f or price_f in (float("inf"), float("-inf")):
        raise MalformedTickError(f"non-finite price: {price!r}")
    epoch = body.get("epoch")
    if epoch is None:
        raise MalformedTickError("missing epoch")
    epoch_i = _parse_strict_epoch(epoch)
    if not instrument or not str(instrument).strip():
        raise MalformedTickError("instrument must be non-empty")
    return LiveTick(
        instrument=str(instrument).strip(),
        symbol=symbol,
        price=price_f,
        epoch=epoch_i,
    )


def _parse_strict_epoch(epoch: object) -> int:
    """Accept only true integers (or integer-valued numeric strings).

    Rejects bool, fractional floats, fractional strings, and non-numeric values.
    Never silently truncates.
    """
    if isinstance(epoch, bool):
        raise MalformedTickError(f"invalid epoch (bool): {epoch!r}")
    if isinstance(epoch, int):
        if epoch < 0:
            raise MalformedTickError(f"negative epoch: {epoch}")
        return epoch
    if isinstance(epoch, float):
        if epoch != epoch or epoch in (float("inf"), float("-inf")):
            raise MalformedTickError(f"non-finite epoch: {epoch!r}")
        if not epoch.is_integer():
            raise MalformedTickError(f"fractional epoch: {epoch!r}")
        epoch_i = int(epoch)
        if epoch_i < 0:
            raise MalformedTickError(f"negative epoch: {epoch_i}")
        return epoch_i
    if isinstance(epoch, str):
        s = epoch.strip()
        if not s:
            raise MalformedTickError("empty epoch string")
        if s[0] in "+-" and s[1:].isdigit():
            epoch_i = int(s)
        elif s.isdigit():
            epoch_i = int(s)
        else:
            raise MalformedTickError(f"invalid epoch string: {epoch!r}")
        if epoch_i < 0:
            raise MalformedTickError(f"negative epoch: {epoch_i}")
        return epoch_i
    raise MalformedTickError(f"invalid epoch type: {type(epoch).__name__}")
