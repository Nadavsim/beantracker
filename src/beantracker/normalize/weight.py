import re
from decimal import Decimal

_NUM = r"(\d+(?:[.,]\d+)?)"


def _unit_re(words: str) -> re.Pattern[str]:
    return re.compile(rf"{_NUM}\s*(?:{words})(?![a-z])", re.IGNORECASE)


# "8oz (227g)" must read as 227, so an explicit gram figure is tried first.
_GRAMS = _unit_re(r"grams?|gr|g|גרם")
_KILOS = _unit_re(r"kilograms?|kilos?|kgs?|קילוגרם|קילו")
_OUNCES = _unit_re(r"ounces?|oz")
_POUNDS = _unit_re(r"pounds?|lbs?")
_BARE_KILO = re.compile(r"(?:קילוגרם|קילו)\s*$")  # "קילו" alone means one kilo

_CONVERSIONS: list[tuple[re.Pattern[str], Decimal]] = [
    (_GRAMS, Decimal(1)),
    (_KILOS, Decimal(1000)),
    (_OUNCES, Decimal("28.3495")),
    (_POUNDS, Decimal("453.592")),
]


def parse_weight_g(*texts: str) -> int | None:
    """Weight in grams from the first text that states one; None rather than a guess."""
    for text in texts:
        if not text:
            continue
        for pattern, factor in _CONVERSIONS:
            m = pattern.search(text)
            if m:
                value = Decimal(m.group(1).replace(",", ".")) * factor
                return int(value.quantize(Decimal(1)))
        if _BARE_KILO.search(text.strip()):
            return 1000
    return None
