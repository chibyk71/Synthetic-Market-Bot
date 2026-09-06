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
    try:
        epoch_i = int(epoch)
    except (TypeError, ValueError) as exc:
        raise MalformedTickError(f"invalid epoch: {epoch!r}") from exc
    if epoch_i < 0:
        raise MalformedTickError(f"negative epoch: {epoch_i}")
    if not instrument or not str(instrument).strip():
        raise MalformedTickError("instrument must be non-empty")
    return LiveTick(
        instrument=str(instrument).strip(),
        symbol=symbol,
        price=price_f,
        epoch=epoch_i,
        raw=dict(body),
    )
