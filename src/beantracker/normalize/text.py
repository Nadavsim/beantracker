import re

from selectolax.lexbor import LexborHTMLParser

LABELS = (
    "Tastes Like",
    "Tasting Notes",
    "Origin",
    "Variety",
    "Varietal",
    "Producer",
    "Elevation",
    "Altitude",
    "Process",
    "Region",
    "Roast",
)
MAX_VALUE_LEN = 120
_LABEL_RE = re.compile(r"\b(" + "|".join(LABELS) + r")\s*:", re.IGNORECASE)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    text = LexborHTMLParser(html).text(separator=" ")
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def clean_quotes(text: str) -> str:
    """Hebrew geresh and typographic quotes vary between sites; fold to ASCII."""
    return text.replace("׳", "'").replace("’", "'").replace("‘", "'")


def labelled_fields(text: str) -> dict[str, str]:
    """Parse 'Label: value Label2: value' runs (as in Hydrangea descriptions).

    A value runs until the next known label, but is also cut at the first sentence end and
    capped: a stray 'Variety:' inside prose must not swallow the rest of the description.
    First occurrence of a label wins.
    """
    fields: dict[str, str] = {}
    matches = list(_LABEL_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        value = text[m.end() : end].strip()
        value = re.split(r"\.\s", value, maxsplit=1)[0][:MAX_VALUE_LEN].strip()
        fields.setdefault(m.group(1).lower(), value)
    return fields
