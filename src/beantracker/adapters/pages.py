"""Readers for the descriptive block on a roaster's product page.

Some Shopify themes render coffee details (country, process, roast level...) from metafields
that `/products.json` does not expose, so the feed's description is empty and only the page
carries them. A reader takes the page's HTML and returns the raw label/value pairs as the
site wrote them, under the attribute names `normalize()` understands. Interpreting the values
(canonical process, roast vocabulary, region cleanup) stays in normalization.
"""

from collections.abc import Callable

from selectolax.lexbor import LexborHTMLParser

# page label (lowercased) -> attribute key; unknown labels keep their own name
_LABEL_KEYS = {
    "country": "origin",
    "region": "region",
    "variety": "varietal",
    "varietal": "varietal",
    "process": "process",
    "roast level": "roast",
}


def _squash(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def parse_parameter_list(html: str) -> dict[str, str]:
    """`<ul class="parameter-list"><li><span class="par-name">Country</span>: Colombia</li>`.

    Returns {} for a page without that list (merchandise, gift cards), which is normal.
    """
    details: dict[str, str] = {}
    for li in LexborHTMLParser(html).css("ul.parameter-list li"):
        name = li.css_first(".par-name")
        if name is None:
            continue
        label = _squash(name.text()).lower()
        value = _squash(li.text().replace(name.text(), "", 1)).lstrip(":").strip()
        if label and value:
            details.setdefault(_LABEL_KEYS.get(label, label.replace(" ", "_")), value)
    return details


# Named in roasters.yaml as `details_from_pages: {parser: <name>}`.
PAGE_PARSERS: dict[str, Callable[[str], dict[str, str]]] = {
    "parameter_list": parse_parameter_list,
}
