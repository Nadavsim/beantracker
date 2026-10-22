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

    A value runs until the next known label, so prose after the last label is the only
    thing that can be over-captured. First occurrence of a label wins.
    """
    fields: dict[str, str] = {}
    matches = list(_LABEL_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields.setdefault(m.group(1).lower(), text[m.end() : end].strip())
    return fields
