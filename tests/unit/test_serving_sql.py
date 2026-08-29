"""The one LIKE escape both read paths share."""

from __future__ import annotations

import inspect

import pytest

from src.serving import catalog, feedback
from src.serving.sql import LIKE_ESCAPE_CHARACTER, escape_like


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("", ""),
        ("blade runner", "blade runner"),
        ("100%", "100\\%"),
        ("_", "\\_"),
        ("a_b%c", "a\\_b\\%c"),
        # The escape character is escaped first, so a viewer who types a
        # backslash gets one literal backslash back rather than an escape
        # sequence that swallows the character after it.
        ("\\", "\\\\"),
        ("\\%", "\\\\\\%"),
        ("C:\\_temp", "C:\\\\\\_temp"),
    ],
)
def test_wildcards_and_the_escape_character_are_escaped_to_match_themselves(
    raw: str, escaped: str
) -> None:
    assert escape_like(raw) == escaped


def test_both_read_paths_spell_the_escape_clause_this_module_escapes_for() -> None:
    """The half of the contract SQL owns.

    ``escape_like`` is only correct next to an ``ESCAPE`` clause naming the same
    character: without one, most dialects read the backslashes it adds as
    literal text and the pattern quietly stops matching. Two modules build those
    patterns, so the coupling is asserted rather than trusted to review.
    """
    clause = f"ESCAPE '{LIKE_ESCAPE_CHARACTER * 2}'"

    for module in (catalog, feedback):
        source = inspect.getsource(module)
        assert source.count("LIKE :") == source.count(clause), module.__name__
