import re
from decimal import Decimal

# Thousands-grouped ('1,000') is tried before a plain decimal ('1,5' or '1.5').
_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:[.,]\d+)?)"
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+")


def _to_decimal(raw: str) -> Decimal:
    if _GROUPED.fullmatch(raw):
        return Decimal(raw.replace(",", ""))
    return Decimal(raw.replace(",", "."))


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


_COUNT_BEFORE = re.compile(r"(\d+)\s*[x×]\s*$", re.IGNORECASE)  # "2 x " before "250g"
_COUNT_AFTER = re.compile(r"\s*[x×]\s*(\d+)(?![\d.,]*\s*(?:g|kg|oz|lb))", re.IGNORECASE)  # " x 2"


def _pack_count(text: str, start: int, end: int) -> int:
    """How many of the stated unit are in the pack: '2 x 250g' and '250g x 2' are 500 g."""
    before = _COUNT_BEFORE.search(text[:start])
    if before:
        return int(before.group(1))
    after = _COUNT_AFTER.match(text, end)
    return int(after.group(1)) if after else 1


def parse_weight_g(*texts: str) -> int | None:
    """Weight in grams from the first text that states one; None rather than a guess."""
    for text in texts:
        if not text:
            continue
        for pattern, factor in _CONVERSIONS:
            m = pattern.search(text)
            if m:
                value = _to_decimal(m.group(1)) * factor * _pack_count(text, m.start(), m.end())
                return int(value.quantize(Decimal(1)))
        if _BARE_KILO.search(text.strip()):
            return 1000
    return None
