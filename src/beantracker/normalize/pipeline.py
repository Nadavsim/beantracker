from datetime import datetime

from beantracker.domain.bean import Bean
from beantracker.domain.raw import RawProduct
from beantracker.normalize.attributes import parse_process, parse_roast, split_notes
from beantracker.normalize.origin import parse_origin
from beantracker.normalize.price import price_per_100g, to_minor
from beantracker.normalize.text import html_to_text, labelled_fields
from beantracker.normalize.weight import parse_weight_g


def normalize(product: RawProduct, roaster: str, currency: str, scraped_at: datetime) -> list[Bean]:
    """RawProduct -> one Bean per sellable size. Pure: no I/O, unknown fields stay None.

    Source precedence for descriptive fields: structured attributes from the adapter, then
    'Label: value' runs in the description, then keywords in the title/description.
    """
    text = html_to_text(product.description)
    fields = labelled_fields(text)
    attrs = product.attributes

    country, region = parse_origin(attrs.get("origin") or fields.get("origin"), product.title, text)
    region = attrs.get("region") or region
    process = parse_process(
        attrs.get("process", ""), fields.get("process", ""), product.title, text
    )
    roast = parse_roast(attrs.get("roast", ""), fields.get("roast", ""), product.title, text)
    varietal = attrs.get("varietal") or fields.get("variety") or fields.get("varietal")
    notes = split_notes(
        attrs.get("tasting_notes") or fields.get("tastes like") or fields.get("tasting notes")
    )

    beans: dict[tuple[int | None, int], Bean] = {}
    for v in product.variants:
        price_minor = to_minor(v.price, currency)
        if price_minor is None:
            continue
        # The title states the size reliably; the site's `grams` is often 0 or copy-pasted.
        weight = parse_weight_g(v.title) or (v.grams or None)
        # Grind options (Nomena sells 10 grinds per size) are not distinct products: collapse
        # variants with the same size and price, and treat the lot as in stock if any is.
        key = (weight, price_minor)
        existing = beans.get(key)
        if existing is not None:
            existing.in_stock = existing.in_stock or v.available
            continue
        beans[key] = Bean(
            roaster=roaster,
            external_id=product.external_id,
            variant_external_id=v.external_id,
            name=product.title,
            url=product.url,
            origin_country=country,
            origin_region=region,
            process=process,
            varietal=varietal,
            roast_level=roast,
            tasting_notes=notes,
            weight_g=weight,
            price_minor=price_minor,
            currency=currency,
            price_per_100g=price_per_100g(price_minor, currency, weight),
            in_stock=v.available,
            scraped_at=scraped_at,
        )
    return list(beans.values())
