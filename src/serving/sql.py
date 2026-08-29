"""Small SQL helpers shared by the read paths.

Both the catalog and the Library take free text from a viewer and drop it into
a ``LIKE`` pattern, so both need the same escaping rule and both have to agree
with the ``ESCAPE`` clause their queries spell. That rule lived twice —
``CatalogService`` could not import ``FeedbackService``'s copy because the
dependency already runs the other way — and two copies of a security-adjacent
string transform is one copy too many: the day one of them learns about a new
metacharacter is the day they disagree.

Dependency-free on purpose, in the same spirit as ``src.serving.policy``: a
module every read path imports must not pull anything into the slim API image.
"""

from __future__ import annotations

__all__ = ["LIKE_ESCAPE_CHARACTER", "escape_like"]

# The escape character the queries name in their own ``ESCAPE`` clause. Spelled
# here as the single character SQL sees, not as the SQL literal, so a caller
# comparing the two is comparing like with like.
LIKE_ESCAPE_CHARACTER = "\\"


def escape_like(value: str) -> str:
    """Escape a viewer's text so ``%`` and ``_`` match themselves.

    Callers must pair this with ``ESCAPE '\\'`` in the query — without it the
    backslashes this adds are literal characters in most dialects and the
    pattern stops matching. The escape character goes first: escaping it after
    the wildcards would double the backslashes this function just introduced.
    """
    return (
        value.replace(LIKE_ESCAPE_CHARACTER, LIKE_ESCAPE_CHARACTER * 2)
        .replace("%", f"{LIKE_ESCAPE_CHARACTER}%")
        .replace("_", f"{LIKE_ESCAPE_CHARACTER}_")
    )
