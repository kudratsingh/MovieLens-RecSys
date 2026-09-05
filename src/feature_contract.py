"""Dependency-free ranker feature schemas shared by training and serving.

There are two contracts, because since ADR 0018 there are two rankers.

``FEATURE_COLUMNS`` is the eight aggregate columns every ranker has ever used
and is the **fallback route's** contract: a user below the cold-start threshold
routes to the popularity slate precisely because their history is too thin to
encode, so the sequence features below are undefined for them by construction.
It is unchanged, and deliberately so — the fallback booster, the serving
sidecar, the baked manifest and the online parity test all read this name.

``LEARNED_ROUTE_FEATURE_COLUMNS`` is the ten columns the **learned route's**
ranker reads: the same eight, in the same order, followed by the two SASRec
score features ADR 0018 requires. The eight come first so a split index learned
against the shorter contract still refers to the same feature, which makes the
two contracts comparable rather than merely adjacent.

Order is load-bearing in both. LightGBM learns splits by column *index*, so a
caller that reordered these would silently score candidates against the wrong
feature per split. Serving manifest schema v2 carries a feature order per route
for exactly this reason.
"""

from __future__ import annotations

FEATURE_COLUMNS: list[str] = [
    "user_interaction_count",
    "user_days_active",
    "user_days_since_last_interaction",
    "item_popularity_all_time",
    "item_popularity_30d",
    "item_popularity_7d",
    "item_age_days",
    "user_genre_affinity",
]

#: The two point-in-time SASRec features ADR 0018 increment 1 requires.
#:
#: ``sasrec_user_item_score`` is the inner product of the L2-normalised user
#: vector and the L2-normalised candidate item embedding. Both sides are
#: normalised at the retrieval boundary, so this *is* the cosine and it is
#: exactly the quantity FAISS ranked the candidate on.
#:
#: ``sasrec_user_item_logit`` is the same inner product taken over the
#: *unnormalised* representations — the quantity the BCE-family objective
#: calibrated, carrying the magnitude the normalised score throws away.
#:
#: Both are NaN when the row has no encodable point-in-time history (a
#: fallback-route positive) or the candidate is outside the encoder's
#: vocabulary. NaN rather than a numeric sentinel: LightGBM has first-class
#: missing handling and learns a default direction per split, and a fabricated
#: score would teach the booster a relation that never holds where the column
#: is actually used.
SASREC_SCORE_COLUMNS: list[str] = [
    "sasrec_user_item_score",
    "sasrec_user_item_logit",
]

#: The learned route's ordered contract: the eight aggregates then the two
#: sequence scores.
LEARNED_ROUTE_FEATURE_COLUMNS: list[str] = [*FEATURE_COLUMNS, *SASREC_SCORE_COLUMNS]

#: A readable alias for the eight, used where the *route* is the point rather
#: than the history of the name.
FALLBACK_ROUTE_FEATURE_COLUMNS: list[str] = FEATURE_COLUMNS
