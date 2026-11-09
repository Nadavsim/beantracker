import re

_BASES: list[tuple[str, str]] = [
    (r"honey|האני|הוני", "Honey"),
    (r"natural|נטורל|נאטורל", "Natural"),
    (r"washed|שטוף", "Washed"),
]
_MODIFIERS: list[tuple[str, str]] = [
    (r"anaerobic|אנאירובי", "Anaerobic"),
    (r"carbonic maceration", "Carbonic Maceration"),
]


def _search(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(rf"(?<!\w)(?:{pattern})(?!\w)", text)


def parse_process(*texts: str) -> str | None:
    """Canonical process such as 'Anaerobic Natural' from the first text naming one.

    Whole-word matches only, so 'Washing Station' is not mistaken for 'Washed'.
    """
    for text in texts:
        lowered = (text or "").lower()
        bases = [(m.start(), label) for pat, label in _BASES if (m := _search(pat, lowered))]
        if not bases:
            continue
        base = min(bases)[1]  # earliest mention wins
        mods = [label for pat, label in _MODIFIERS if _search(pat, lowered)]
        return " ".join([*mods, base])
    return None


_MARKER = re.compile(r"(?<!\w)(?:process(?:ed|ing)?|עיבוד|תהליך)(?!\w)")


def parse_process_stated(text: str) -> str | None:
    """Process from free prose, only when the text says it is talking about processing.

    A bare 'natural sweetness' or 'notes of honey' must not set the process, so a base
    word counts only within 30 characters of 'process', 'processed' or the Hebrew
    equivalents.
    """
    lowered = (text or "").lower()
    for marker in _MARKER.finditer(lowered):
        window = lowered[max(0, marker.start() - 30) : marker.end() + 30]
        found = parse_process(window)
        if found:
            return found
    return None


_ROAST_RULES: list[tuple[str, str]] = [
    (r"medium[- ]dark|בינונית[- ]כהה", "medium-dark"),
    # Before "light": "medium-light roast" also contains "light roast".
    (r"(?:medium[- ]light|light[- ]medium)[- ]roast", "medium-light"),
    (r"(?:extra|ultra)[- ]?light|light[- ]roast|קלי?יה בהירה", "light"),
    (r"medium[- ]roast|קלי?יה בינונית", "medium"),
    (r"dark[- ]roast|קלי?יה כהה", "dark"),
]


def parse_roast(*texts: str) -> str | None:
    """Roast level from explicit roast phrasing only, never from words like 'light body'."""
    for text in texts:
        lowered = (text or "").lower()
        for pattern, label in _ROAST_RULES:
            if re.search(pattern, lowered):
                return label
    return None


def parse_roast_label(value: str) -> str | None:
    """Roast level from a field that is only about roast ('Medium - Light', 'Dark').

    Unlike `parse_roast`, which must find roast phrasing inside prose, a dedicated
    'Roast Level' value can be read by its words alone. Order does not matter, so
    'Medium - Light' and 'Light - Medium' agree.
    """
    words = set(re.findall(r"light|medium|dark", (value or "").lower()))
    if {"medium", "dark"} <= words:
        return "medium-dark"
    if {"medium", "light"} <= words:
        return "medium-light"
    for word in ("dark", "light", "medium"):
        if word in words:
            return word
    return None


def split_notes(value: str | None) -> list[str]:
    if not value:
        return []
    return [n.strip(" .") for n in re.split(r"[,;·•|]", value) if n.strip(" .")]
