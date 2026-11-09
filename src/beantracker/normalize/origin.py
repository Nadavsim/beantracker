import re

from beantracker.normalize.text import clean_quotes

# canonical country -> aliases (English + Hebrew), matched as whole words, case-insensitive.
COUNTRIES: dict[str, tuple[str, ...]] = {
    "Ethiopia": ("ethiopia", "אתיופיה"),
    "Kenya": ("kenya", "קניה", "קנייה"),
    "Rwanda": ("rwanda", "רואנדה"),
    "Burundi": ("burundi", "בורונדי"),
    "Uganda": ("uganda", "אוגנדה"),
    "Tanzania": ("tanzania", "טנזניה"),
    "Yemen": ("yemen", "תימן"),
    "Colombia": ("colombia", "קולומביה"),
    "Brazil": ("brazil", "ברזיל"),
    "Peru": ("peru", "פרו"),
    "Bolivia": ("bolivia", "בוליביה"),
    "Ecuador": ("ecuador", "אקוודור"),
    "Panama": ("panama", "פנמה"),
    "Costa Rica": ("costa rica", "קוסטה ריקה"),
    "Nicaragua": ("nicaragua", "ניקרגואה"),
    "Honduras": ("honduras", "הונדורס"),
    "El Salvador": ("el salvador", "אל סלבדור"),
    "Guatemala": ("guatemala", "גואטמלה"),
    "Mexico": ("mexico", "מקסיקו"),
    "Jamaica": ("jamaica", "ג'מייקה"),
    "India": ("india", "הודו"),
    "Indonesia": ("indonesia", "אינדונזיה"),
    "Papua New Guinea": ("papua new guinea", "פפואה גינאה החדשה"),
    "Vietnam": ("vietnam", "וייטנאם"),
    "Thailand": ("thailand", "תאילנד"),
    "China": ("china", "סין"),
    "Myanmar": ("myanmar", "מיאנמר"),
}

# region -> (country, aliases). Deliberately small: only unambiguous, well-known regions.
REGIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Guji": ("Ethiopia", ("guji", "גוג'י")),
    "Yirgacheffe": ("Ethiopia", ("yirgacheffe", "yirgachefe")),
    "Sidama": ("Ethiopia", ("sidama", "sidamo")),
    "Jimma": ("Ethiopia", ("jimma", "ג'ימה")),
    "Gedeb": ("Ethiopia", ("gedeb",)),
    "Nyeri": ("Kenya", ("nyeri",)),
    "Kirinyaga": ("Kenya", ("kirinyaga",)),
    "Huila": ("Colombia", ("huila",)),
    "Nariño": ("Colombia", ("nariño", "narino")),
    "Cauca": ("Colombia", ("cauca", "קאוקה")),
    "Tolima": ("Colombia", ("tolima",)),
    "Antioquia": ("Colombia", ("antioquia",)),
    "Tarrazú": ("Costa Rica", ("tarrazu", "tarrazú")),
    "Gedeo": ("Ethiopia", ("gedeo",)),
    "Chiriquí": ("Panama", ("chiriqui", "chiriquí")),
    "Quindío": ("Colombia", ("quindio", "quindío")),
    "Yunnan": ("China", ("yunnan",)),
}


def _contains(text: str, alias: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, re.IGNORECASE) is not None


def find_country(text: str) -> str | None:
    """The single country mentioned in text.

    None when absent or when several appear: a blend or multi-origin lot is not given an
    invented single origin.
    """
    text = clean_quotes(text)
    found = {c for c, aliases in COUNTRIES.items() if any(_contains(text, a) for a in aliases)}
    return found.pop() if len(found) == 1 else None


def find_region(text: str) -> tuple[str, str] | None:
    """(region, country) for a known region mentioned in text."""
    text = clean_quotes(text)
    for region, (country, aliases) in REGIONS.items():
        if any(_contains(text, a) for a in aliases):
            return region, country
    return None


def _labelled_region(before_country: list[str], country: str) -> str | None:
    """Region from the parts preceding the country in 'Farm, Region, Country'.

    A known region anywhere in them wins (and is returned in canonical spelling). Without
    one, only the exact three-part shape is trusted: with two parts the first is usually
    the farm, and with four or more the middle ones are localities, not regions.
    """
    for part in before_country:
        hit = find_region(part)
        if hit and hit[1] == country:
            return hit[0]
    return before_country[-1] if len(before_country) == 2 else None


def parse_origin(origin_field: str | None, *fallback_texts: str) -> tuple[str | None, str | None]:
    """(country, region).

    A labelled origin like 'Finca El Turpial, Nariño, Colombia' is authoritative for the
    country (the last comma part). The region is only taken when it is trustworthy, see
    _labelled_region. Otherwise fall back to keyword search over the given texts.
    """
    if origin_field:
        parts = [p.strip() for p in origin_field.split(",") if p.strip()]
        country = find_country(parts[-1]) if parts else None
        if country:
            return country, _labelled_region(parts[:-1], country)
        country = find_country(origin_field)
        if country:
            return country, None
    country = next((c for t in fallback_texts if (c := find_country(t))), None)
    region = None
    for text in fallback_texts:
        hit = find_region(text)
        if hit and (country is None or hit[1] == country):
            region = hit[0]
            country = country or hit[1]
            break
    return country, region
