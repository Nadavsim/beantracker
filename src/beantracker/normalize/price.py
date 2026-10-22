from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO 4217 minor-unit exponents that differ from the default of 2.
_EXPONENT = {"JPY": 0, "KRW": 0, "CLP": 0, "ISK": 0, "BHD": 3, "KWD": 3}


def _exponent(currency: str) -> int:
    return _EXPONENT.get(currency.upper(), 2)


def to_minor(price: str, currency: str) -> int | None:
    """'27.50','USD' -> 2750. Accepts '1,234.50' and '27,50'; None if unparseable."""
    cleaned = "".join(ch for ch in price if ch.isdigit() or ch in ".,")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")  # comma is a thousands separator
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        # '27,50' is a decimal comma; '1,234' is thousands.
        cleaned = f"{head}.{tail}" if len(tail) != 3 else cleaned.replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return int(
        (value * Decimal(10) ** _exponent(currency)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


def price_per_100g(price_minor: int, currency: str, weight_g: int | None) -> Decimal | None:
    if not weight_g:
        return None
    major = Decimal(price_minor) / Decimal(10) ** _exponent(currency)
    return (major / weight_g * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
