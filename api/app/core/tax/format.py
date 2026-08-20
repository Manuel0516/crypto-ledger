from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_MONEY_QUANT = Decimal("0.01")
_QUANTITY_QUANT = Decimal("0.00000001")  # 8 decimal places — covers satoshi-level precision


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(0)


def format_money(value) -> str:
    """Fiat amounts, rounded to 2 decimal places. Never scientific notation —
    RP2's raw ODS output and Decimal division both leak either long repeating
    decimals or (via a plain float's str()) 1e-05-style notation; both break
    a fixed-width table cell and CSV consumers."""
    return str(_to_decimal(value).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP))


def format_quantity(value) -> str:
    """Crypto quantities, rounded to 8 decimal places with trailing zeros
    stripped — fixed-point always (format(d, 'f')), so 0.00001 never becomes
    '1e-05'."""
    quantized = _to_decimal(value).quantize(_QUANTITY_QUANT, rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
