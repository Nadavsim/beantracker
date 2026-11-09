import pytest

from beantracker.normalize.attributes import parse_process_adjective


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A washed Geisha from Hacienda La Esmeralda in Boquete, Panama.", "Washed"),
        ("A natural Geisha from José Luis Chevas in Boquete.", "Natural"),
        ("A floral washed Typica with notes of citrus, from Jose Guevara.", "Washed"),
        ("An elegant honey Catuai from Costa Rica.", "Honey"),
        # Not a process: no article, a lowercase next word, or the word is a flavour.
        ("Natural Geisha from Panama.", None),
        ("A natural sweetness with notes of plum.", None),
        ("Soft red berries and sweet dark chocolate.", None),
        ("", None),
    ],
)
def test_parse_process_adjective(text: str, expected: str | None) -> None:
    assert parse_process_adjective(text) == expected
