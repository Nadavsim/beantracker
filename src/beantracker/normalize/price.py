from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO 4217 minor-unit exponents that differ from the default of 2.
_EXPONENT = {"JPY": 0, "KRW": 0, "CLP": 0, "ISK": 0, "BHD": 3, "KWD": 3}


def _exponent(currency: str) -> int:
    return _EXPONENT.get(currency.upper(), 2)


def _plain_number(text: str, exponent: int) -> str:
    """Rewrite '1.234,50', '1,234.50', '27,50', '1,234' as a plain decimal string.

    With both separators the last one is the decimal point ('1.234,50' is European).
    With one kind, exactly three trailing digits mean thousands ('1,234'), unless the
    currency itself has three decimals; otherwise it is the decimal point ('27,50').
    """
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    if last_dot != -1 and last_comma != -1:
        decimal = "." if last_dot > last_comma else ","
        thousands = "," if decimal == "." else "."
        return text.replace(thousands, "").replace(decimal, ".")
    if last_dot == -1 and last_comma == -1:
        return text
    sep = "," if last_comma != -1 else "."
    parts = text.split(sep)
    if len(parts) > 2 or (len(parts[-1]) == 3 and exponent != 3):
        return "".join(parts)
    return f"{parts[0]}.{parts[1]}"


def to_minor(price: str, currency: str) -> int | None:
    """'27.50','USD' -> 2750. Accepts '1,234.50' and '27,50'; None if unparseable."""
    cleaned = "".join(ch for ch in price if ch.isdigit() or ch in ".,")
    if not cleaned:
        return None
    try:
        value = Decimal(_plain_number(cleaned, _exponent(currency)))
    except InvalidOperation:
        return None
    return int(
        (value * Decimal(10) ** _exponent(currency)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


def from_minor(price_minor: int, currency: str) -> Decimal:
    """Minor units back to a displayable amount: 2750 USD -> 27.50, 3000 JPY -> 3000.

    Quantized so trailing zeros survive ('27.50', not '27.5')."""
    exponent = _exponent(currency)
    return (Decimal(price_minor) / Decimal(10) ** exponent).quantize(Decimal(10) ** -exponent)


def price_per_100g(price_minor: int, currency: str, weight_g: int | None) -> Decimal | None:
    if not weight_g:
        return None
    major = Decimal(price_minor) / Decimal(10) ** _exponent(currency)
    return (major / weight_g * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
