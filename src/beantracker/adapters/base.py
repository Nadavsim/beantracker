from typing import Protocol

from beantracker.domain.raw import RawProduct


class RoasterAdapter(Protocol):
    """Fetches a roaster's current products. No normalization, no persistence."""

    slug: str

    async def fetch(self) -> list[RawProduct]: ...
