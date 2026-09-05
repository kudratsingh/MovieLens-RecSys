"""
Content-based retrieval for items nobody has consumed — ADR 0017, increment 1.

Every other retriever here derives an item's representation from interactions.
Item-item builds neighbours from co-occurrence; the two-tower and SASRec learn
item embeddings from consumption. That works exactly as long as an item has been
consumed and fails completely when it has not — not because the item ranks
poorly, but because there is nothing in its entry to rank. On the committed
MovieLens 25M snapshot 3,376 catalog movies have no rating at all, and no
interaction-derived index can reach one of them however deep you ask.

This model represents an item by what it *is* rather than by who watched it, so
an item with zero interactions is scored exactly like any other. Two attributes
carry it, both already in the ``movies`` table and both DVC-tracked:

  **Genres.** A pipe-separated set per film, 20 distinct values on this
  snapshot, present for 91.9% of the catalog and 85.4% of the cold items.
  **Release year.** Parsed off the trailing ``(YYYY)`` of the title, present for
  99.3% of the catalog and 98.9% of the cold items.

ADR 0017's measured reason for stopping there is worth restating, because the
obvious richer signal is a trap: the tag genome covers 22.1% of the catalog and
**0.00% of the cold items**, since it is computed from user-applied tags and so
exists only for films that already have engagement. It is inversely correlated
with the need it would serve. TMDB metadata is what eventually covers the 14.6%
of cold items with no genres listed; it is a separate ingestion with its own
rate-limit, caching and snapshot-versioning questions and is deliberately not
here.

**What this rung can claim.** Coverage — cold items become reachable at all —
is the primary claim and it is a property of the mechanism, provable by
inspection. Relevance is secondary and weakly measured: ADR 0017's cold-item
slice is 829 holdout rows over 313 users, and under the 2026-09-05 one-run
policy there is no seed spread either. Nothing here should be read as evidence
that users are better served; a cold-item recall figure from this model is
evidence that the machinery works. The offline population is also not the
production one — offline a "cold item" is a deep-catalog obscurity, in serving
it is a film released this week, and MovieLens ends in 2019 so this dataset
cannot measure the second case at all.

**Scoring.** A user's taste profile is the mean of the L2-normalized genre
indicator vectors of the films they watched strictly before the query time,
renormalized to unit length. Each film contributes one unit of genre mass spread
across its genres, so a four-genre film does not count four times as much as a
one-genre film — the same one-film-one-vote accounting ``user_genre_affinity``
uses in ``src/features/pipeline.py``. A candidate is scored by cosine against
that profile, which is a graded refinement of the same aggregate: the feature
asks "did the genre sets intersect at all?", this asks "how much of the genre
mass overlaps?". Two runs of the same history therefore cannot disagree about a
user's taste in the way an independently-invented profile could.

Nineteen genres over a few hundred observed combinations is a coarse space, so
the cosine ties in large blocks and something has to order each block. Release
year does it, as the distance from a candidate's year to the *nearest* year the
user actually watched. Ties on that are broken by movie id, which is what makes
the ordering total and the model reproducible down to the tie. The three keys
are lexicographic rather than blended into one number on purpose: a blend needs
a mixing weight nobody has evidence to set, and a rung whose honest claim is
"coverage" should not ship a tuned knob that makes its relevance number look
chosen.

Two deliberate departures worth arguing with:

  *Era proximity, not recency.* ADR 0017 calls the year term "a recency prior".
  A global preference for newer films is a production-shaped guess — it would
  help the film released this week, which is the case that motivates the rung
  and the case this dataset cannot score. Offline it would systematically demote
  precisely the pre-2000 B-movies the cold population is made of. So the year is
  used as proximity to the user's own era rather than as a bias toward new, and
  the production variant is left for whoever can measure it.

  *No popularity anywhere in the ranking.* ``last_item`` breaks its ties by
  training popularity and backfills short lists from the popularity ranking.
  Both would be actively wrong here: a cold item has popularity zero by
  definition, so a popularity tie-break loses it every tie it enters, and a
  popularity backfill would let warm items quietly inflate a recall number this
  model is supposed to be measured on. Popularity appears only where every
  sibling puts it — as the cold-*user* fallback below ADR 0001's threshold.

**Cost.** Fit is one stable sort of the training frame plus a catalog-sized
parse; there is nothing learned. A query scores the whole catalog (~62 k × 20)
and lexsorts it, which is a few milliseconds — small next to the other
retrievers' fits, and bounded by the catalog rather than by the user's history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import routing
from .popularity import PopularityModel

# MovieLens's sentinel for a film with no genres. Treated as the empty set so it
# can never match, exactly as ``src/features/pipeline._parse_genre_set`` does —
# a genre match against a genre-less item would be a false signal, and the two
# places that read this column must agree about what the string means.
MISSING_GENRES = "(no genres listed)"

# MovieLens puts the release year in the title, as a trailing ``(YYYY)``. A
# small number of titles have none; those items carry no era signal and say so
# rather than being assigned a plausible year.
_YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")

_REQUIRED_MOVIE_COLUMNS = ("movieId", "title", "genres")
_REQUIRED_RATING_COLUMNS = ("userId", "movieId", "timestamp")


class DuplicateCatalogItemError(ValueError):
    """The catalog listed one ``movieId`` twice with, potentially, two contents.

    Raised rather than resolved by picking a row. ``movies.movieId`` is a primary
    key in ``src/data/schema.py``, so a duplicate means the frame did not come
    from the table this model is specified against — and silently keeping the
    last row would give the item a content vector nobody chose.
    """


@dataclass(frozen=True)
class ContentCoverage:
    """What the content representation actually reaches, counted rather than assumed.

    Coverage is this rung's primary claim, so it is a measured attribute of the
    fitted model rather than something a reader derives from the ADR's table.
    The cold-item counts are the ones that matter: they say how much of the
    population this increment can serve *by construction*, before any relevance
    question is asked.
    """

    # The whole catalog, which is what this model ranks over — not the items
    # with interactions, which is what every other retriever ranks over.
    n_catalog_items: int
    n_genres: int
    n_items_with_genres: int
    n_items_with_release_year: int

    # Items with at least one interaction in the fitted frame. The complement is
    # the cold population: reachable here, unreachable by co-occurrence.
    n_interaction_items: int
    n_cold_items: int
    n_cold_items_with_genres: int
    n_cold_items_with_release_year: int

    # Training rows naming a movie the catalog does not contain. Zero on a clean
    # MovieLens ingest; a non-zero value means some history could not be turned
    # into genre mass and the profiles are built from less than they look like.
    n_history_rows_outside_catalog: int

    @property
    def n_cold_items_without_genres(self) -> int:
        """The population increment 1 cannot serve, stated as its own number.

        These items score zero against every profile, so they sit in the
        no-overlap block that a top-500 slate never reaches. ADR 0017 puts them
        at 14.6% of the cold items and says what happens to them today: nothing.
        They are the argument for the TMDB increment, not a rounding error.
        """
        return self.n_cold_items - self.n_cold_items_with_genres


_EMPTY_COVERAGE = ContentCoverage(
    n_catalog_items=0,
    n_genres=0,
    n_items_with_genres=0,
    n_items_with_release_year=0,
    n_interaction_items=0,
    n_cold_items=0,
    n_cold_items_with_genres=0,
    n_cold_items_with_release_year=0,
    n_history_rows_outside_catalog=0,
)


@dataclass(frozen=True)
class ItemContentIndex:
    """One content vector per catalog item, derived from ``movies``.

    This is C-02 on its own: the representation, with no notion of a user in it,
    so it can be built and inspected without fitting a retriever and so a later
    increment can widen it (TMDB overviews, keywords, cast) without touching the
    scoring. Every array is aligned to :attr:`item_ids`, which is sorted, so a
    movie id maps to a row by ``np.searchsorted`` and the layout is canonical
    rather than dependent on the order the frame arrived in.

    The genre vocabulary is derived from the catalog handed in, never hardcoded.
    MovieLens 25M happens to use twenty labels; a snapshot that adds one gets it
    without an edit here, and — more usefully — a fixture with three genres
    produces a three-dimensional space, which is what makes the unit tests
    readable.
    """

    # Sorted, unique. The retrievable universe: the whole catalog, including the
    # items no user has ever touched.
    item_ids: np.ndarray
    # Sorted genre labels, excluding MovieLens's "(no genres listed)" sentinel.
    genres: tuple[str, ...]
    # (n_items, n_genres) float64, L2-normalized per row. A genre-less item is an
    # all-zero row: it scores zero against every profile, which is the honest
    # answer when there is no genre evidence either way, rather than a match.
    # Stored double rather than single because these numbers decide an ordering
    # whose ties are broken exactly, and the whole catalog at this width is ten
    # megabytes — a cost worth paying once to keep every later arithmetic step in
    # one precision.
    genre_vectors: np.ndarray
    # Release year per item as float64, NaN where the title carried none. Float
    # rather than a nullable int so the era arithmetic below stays vectorized.
    release_years: np.ndarray

    @property
    def n_items(self) -> int:
        return int(self.item_ids.size)

    @property
    def has_genres(self) -> np.ndarray:
        """Per-item boolean: does this item carry any genre at all?"""
        return np.asarray(self.genre_vectors.any(axis=1))

    @property
    def has_release_year(self) -> np.ndarray:
        """Per-item boolean: did the title carry a parseable ``(YYYY)``?"""
        return np.asarray(~np.isnan(self.release_years))

    def positions_for(self, movie_ids: np.ndarray) -> np.ndarray:
        """Row index of each movie id, or ``-1`` for an id the catalog lacks.

        A sentinel rather than an exception: a rating naming an unknown movie is
        a data-integrity smell worth counting, but it is not worth failing a
        25 M-row training run over, and the count is published on
        :class:`ContentCoverage` so it cannot pass unnoticed.
        """
        if self.item_ids.size == 0:
            return np.full(movie_ids.shape, -1, dtype=np.int64)
        candidates = np.searchsorted(self.item_ids, movie_ids)
        clipped = np.clip(candidates, 0, self.item_ids.size - 1)
        found = self.item_ids[clipped] == movie_ids
        return np.where(found, clipped, -1).astype(np.int64)


def parse_genres(raw: object) -> tuple[str, ...]:
    """MovieLens's pipe-separated ``genres`` string as a sorted, de-duplicated tuple.

    The sentinel and the empty string both mean "no genres", and blank tokens
    from a stray separator are dropped rather than becoming a genre named "".
    """
    value = "" if raw is None else str(raw)
    if not value or value == MISSING_GENRES or value == "nan":
        return ()
    return tuple(sorted({token for token in value.split("|") if token and token != MISSING_GENRES}))


def parse_release_year(title: object) -> int | None:
    """The trailing ``(YYYY)`` of a MovieLens title, or ``None`` if it has none.

    Anchored to the end of the string on purpose: plenty of titles contain a
    parenthesised year mid-string (alternate titles, re-releases), and taking
    the first match would read a different film's year for some of them.
    """
    match = _YEAR_PATTERN.search(str(title))
    return int(match.group(1)) if match is not None else None


def build_item_content_index(movies: pd.DataFrame) -> ItemContentIndex:
    """Derive the content representation for a whole catalog.

    Expects ``movieId``, ``title`` and ``genres`` — the three columns
    ``src/data/schema.py`` gives the ``movies`` table. Nothing else is consulted,
    and in particular no interaction data is: that is the entire point, and it is
    what lets the index cover an item with no ratings.
    """
    missing = [column for column in _REQUIRED_MOVIE_COLUMNS if column not in movies.columns]
    if missing:
        raise KeyError(f"movies is missing required columns: {missing}")

    if movies.empty:
        return ItemContentIndex(
            item_ids=np.empty(0, dtype=np.int64),
            genres=(),
            genre_vectors=np.empty((0, 0), dtype=np.float64),
            release_years=np.empty(0, dtype=np.float64),
        )

    ordered = movies.sort_values("movieId", kind="stable")
    item_ids = ordered["movieId"].to_numpy(dtype=np.int64)
    duplicates = np.flatnonzero(item_ids[1:] == item_ids[:-1])
    if duplicates.size:
        raise DuplicateCatalogItemError(
            f"movies lists {duplicates.size} duplicated movieId(s), the first being "
            f"{int(item_ids[duplicates[0]])}; a catalog with two rows per item has no "
            "single content vector for that item"
        )

    per_item_genres = [parse_genres(raw) for raw in ordered["genres"]]
    vocabulary = tuple(sorted({genre for genres in per_item_genres for genre in genres}))
    position_of = {genre: index for index, genre in enumerate(vocabulary)}

    genre_vectors = np.zeros((len(per_item_genres), len(vocabulary)), dtype=np.float64)
    for row, genres in enumerate(per_item_genres):
        if not genres:
            continue
        # Unit length per item, so a film labelled with four genres contributes
        # one unit of evidence to a profile rather than four. Written as an
        # explicit reciprocal square root because every genre indicator is 1.0,
        # which makes the norm exactly sqrt(len(genres)).
        weight = 1.0 / np.sqrt(len(genres))
        for genre in genres:
            genre_vectors[row, position_of[genre]] = weight

    release_years = np.array(
        [
            np.nan if (year := parse_release_year(title)) is None else float(year)
            for title in ordered["title"]
        ],
        dtype=np.float64,
    )

    return ItemContentIndex(
        item_ids=item_ids,
        genres=vocabulary,
        genre_vectors=genre_vectors,
        release_years=release_years,
    )


@dataclass
class ContentSimilarityModel:
    """Retrieve by content similarity, so an item with no interactions is reachable.

    Holds the same retrieval contract every other candidate generator honours —
    ``recommend(user_id, k)``, seen items excluded, cold users routed to the
    embedded popularity fallback on ADR 0001's threshold — so a run of this model
    is directly comparable to an item-item or last-item run and could later join
    the same slate.

    ``recommend`` also takes an ``as_of`` timestamp. At ``None`` (the offline
    shape) the profile is the user's whole fitted history, which is already
    strictly before the split cutoff because the caller passes the train slice.
    With a timestamp it is the history strictly before that instant, which is
    what a serving path or a per-event evaluation needs — and what makes the
    equal-timestamp rule below testable directly rather than by inspection.
    """

    # Where the learned path stops, in the same vocabulary every other candidate
    # model uses. ADR 0001's threshold by default; None is the index-membership
    # opt-out. See src/models/candidates/routing.py.
    cold_start_threshold: int | None = routing.DEFAULT_COLD_START_THRESHOLD

    coverage: ContentCoverage = _EMPTY_COVERAGE

    # Populated by fit. The history is stored CSR-style — one chronological run
    # per user inside three parallel arrays, with a span dict on top — for the
    # reason last_item.py gives: a dict of per-user numpy arrays over 25 M rows
    # costs more in object overhead than in data, and these arrays slice
    # straight into the vectorized scoring.
    _index: ItemContentIndex | None = None
    _history_timestamps: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    _history_items: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    _history_positions: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    _history_span: dict[int, tuple[int, int]] = field(default_factory=dict)
    _is_cold_item: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=bool))
    _popularity: PopularityModel = field(default_factory=PopularityModel)

    def fit(self, train: pd.DataFrame, movies: pd.DataFrame) -> ContentSimilarityModel:
        """Build the content index and the per-user histories that query it.

        Two frames rather than one, and the asymmetry is the model: ``movies``
        defines *what can be retrieved* and ``train`` defines *what a user's
        taste is*. Every other retriever in this package collapses the two —
        their retrievable universe is whatever appeared in ``train`` — which is
        exactly why they cannot reach a cold item.

        Expects ``userId``, ``movieId`` and ``timestamp`` on ``train`` and
        ``movieId``, ``title``, ``genres`` on ``movies``. Rating values are
        ignored; every interaction has weight 1.0 per ADR 0002. Nothing but the
        rows handed in is ever consulted, which is what keeps the model
        point-in-time at the frame level: the caller passes the ``t < cutoff``
        slice, so no holdout interaction can enter a profile.
        """
        # Fitted first so the fallback is ready even when there is no history to
        # build profiles from.
        self._popularity = PopularityModel().fit(train)
        self._index = build_item_content_index(movies)
        self._reset_histories()

        if train.empty:
            self.coverage = self._coverage(np.empty(0, dtype=np.int64), 0)
            return self

        missing = [column for column in _REQUIRED_RATING_COLUMNS if column not in train.columns]
        if missing:
            raise KeyError(f"train is missing required columns: {missing}")

        # Sorted by movie id as well as by time so that rows sharing a second
        # arrive in one fixed order. Nothing about the ranking depends on the
        # order within a second, but the profile is a float sum over these rows,
        # and float addition is not associative — without the third key the same
        # data in a different row order could produce a profile that differs in
        # the last bit and therefore a different tie ordering.
        ordered = train.sort_values(["userId", "timestamp", "movieId"], kind="stable")
        users = ordered["userId"].to_numpy(dtype=np.int64)
        self._history_timestamps = ordered["timestamp"].to_numpy(dtype=np.int64)
        self._history_items = ordered["movieId"].to_numpy(dtype=np.int64)
        self._history_positions = self._index.positions_for(self._history_items)

        opens_run = np.empty(len(ordered), dtype=bool)
        opens_run[0] = True
        opens_run[1:] = users[1:] != users[:-1]
        starts = np.flatnonzero(opens_run)
        ends = np.append(starts[1:], len(ordered))
        self._history_span = {
            int(users[start]): (int(start), int(end))
            for start, end in zip(starts, ends, strict=True)
        }

        self.coverage = self._coverage(
            np.unique(self._history_items),
            int(np.count_nonzero(self._history_positions < 0)),
        )
        return self

    def recommend(
        self,
        user_id: int,
        k: int,
        *,
        as_of: int | None = None,
        filter_seen: bool = True,
    ) -> list[int]:
        """Top-k items for one user.

        Cold user (below the routing threshold, or with no usable history at
        ``as_of``) → popularity fallback. Warm user → the catalog ordered by
        content similarity to their taste profile.

        ``filter_seen`` defaults to True — the serving shape, and the shape every
        offline number here is computed under. The ranker training pipeline's
        reason for passing False (a sampled positive is always in the user's own
        history and would otherwise be dropped before it could become a
        LambdaRank positive) applies to this retriever identically, so the knob
        exists before the integration that needs it.
        """
        positions, seen = self._history_before(user_id, as_of)
        if not self._learned_path_serves(user_id, positions.size, seen):
            return self._fallback(k, seen if filter_seen else np.empty(0, dtype=np.int64))

        # Implied by the predicate above, which refuses an unfitted model;
        # stated for the type checker, which cannot see through it.
        assert self._index is not None
        profile = self._profile(positions)
        excluded = seen if filter_seen else np.empty(0, dtype=np.int64)
        return self._rank(profile, positions, excluded, k)

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        """Batch variant — one ``list[int]`` per user, keyed by user id."""
        return {user_id: self.recommend(user_id, k) for user_id in user_ids}

    @property
    def cold_item_ids(self) -> np.ndarray:
        """Catalog items with no interaction in the fitted frame, ascending.

        The population this rung exists for: unreachable by every
        interaction-derived retriever in the package, and ranked here like any
        other item. Published because "how many of these turned up in a slate?"
        is the coverage measurement, and it should be answerable from the model
        rather than re-derived from the frames by whoever asks.
        """
        if self._index is None:
            return np.empty(0, dtype=np.int64)
        return np.asarray(self._index.item_ids[self._is_cold_item])

    def was_served_by_content(self, user_id: int, as_of: int | None = None) -> bool:
        """Predicate: does ``recommend`` route this user to content or to popularity?

        ``recommend`` reaches the same helper this does rather than restating the
        condition, so the two cannot drift — the contract
        ``ItemItemModel.was_served_by_itemitem`` and
        ``LastItemTransitionModel.was_served_by_last_item`` hold, and the one
        every per-policy metric and ADR 0011 bucket count is computed from.

        Like its siblings it says nothing about whether the profile it would
        build has any genre mass in it. Routing and reach are separate claims,
        and a user whose entire history is genre-less films is still *routed* to
        content — see :meth:`content_profile` for what they actually get.

        The default ``as_of`` of ``None`` keeps this a one-argument callable, so
        it can be handed to ``evaluate`` as the routing predicate unchanged.
        """
        positions, seen = self._history_before(user_id, as_of)
        return self._learned_path_serves(user_id, positions.size, seen)

    def content_profile(self, user_id: int, as_of: int | None = None) -> np.ndarray | None:
        """The user's unit-length taste vector over the genre vocabulary.

        ``None`` when the popularity fallback would answer instead. An all-zero
        vector when the user is served by content but every film they watched is
        genre-less: their genre key is then constant across the catalog, the
        lexicographic order collapses to era proximity and then movie id, and
        what they get is an era-matched slice of the catalog rather than a taste
        match. That is a weak answer and it is reported rather than repaired —
        routing them to popularity instead would be *better* recommendations but
        would quietly put popularity results inside the content-served slice,
        and this rung's numbers are worth less than that attribution. On
        MovieLens the case needs every one of a user's ten-plus films to carry no
        genre, so it is essentially unreachable; the trainer counts it anyway,
        because "essentially" is not a measurement.
        """
        positions, seen = self._history_before(user_id, as_of)
        if not self._learned_path_serves(user_id, positions.size, seen):
            return None
        return self._profile(positions)

    # ---- internals -------------------------------------------------------

    def _reset_histories(self) -> None:
        self._history_timestamps = np.empty(0, dtype=np.int64)
        self._history_items = np.empty(0, dtype=np.int64)
        self._history_positions = np.empty(0, dtype=np.int64)
        self._history_span = {}
        self._is_cold_item = np.empty(0, dtype=bool)
        self.coverage = _EMPTY_COVERAGE

    def _coverage(
        self, interaction_items: np.ndarray, n_history_rows_outside_catalog: int
    ) -> ContentCoverage:
        """Count what the fitted representation reaches, cold items apart."""
        assert self._index is not None
        index = self._index
        is_cold = ~np.isin(index.item_ids, interaction_items)
        self._is_cold_item = is_cold
        return ContentCoverage(
            n_catalog_items=index.n_items,
            n_genres=len(index.genres),
            n_items_with_genres=int(np.count_nonzero(index.has_genres)),
            n_items_with_release_year=int(np.count_nonzero(index.has_release_year)),
            n_interaction_items=int(interaction_items.size),
            n_cold_items=int(np.count_nonzero(is_cold)),
            n_cold_items_with_genres=int(np.count_nonzero(is_cold & index.has_genres)),
            n_cold_items_with_release_year=int(np.count_nonzero(is_cold & index.has_release_year)),
            n_history_rows_outside_catalog=n_history_rows_outside_catalog,
        )

    def _history_before(self, user_id: int, as_of: int | None) -> tuple[np.ndarray, np.ndarray]:
        """The user's history strictly before ``as_of``: catalog rows, and seen ids.

        **This is where point-in-time correctness lives.** ``searchsorted`` with
        ``side="left"`` cuts at the first row whose timestamp is *not less than*
        ``as_of``, so an interaction at exactly ``as_of`` is excluded — and with
        it the whole equal-timestamp problem. MovieLens records whole seconds, so
        a user can have several films at one instant; if any of them were allowed
        into the profile for a query at that instant, each would be helping to
        retrieve its siblings, which is the sequence-construction leakage
        CLAUDE.md singles out. Cutting strictly means none of them informs any
        other. It is the same ``bisect_left`` rule ``FeatureIndex`` applies and
        the same one ``temporal_split`` applies at the cutoff, so "strictly
        before" means one thing across the project.

        Returned as two arrays because they are two different things: the
        positions are catalog rows and drop any id the catalog lacks, while the
        seen ids are every movie the user touched, catalog member or not, since
        exclusion must not depend on whether we could represent the item.
        """
        span = self._history_span.get(user_id)
        if span is None:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)

        start, end = span
        if as_of is not None:
            end = start + int(
                np.searchsorted(self._history_timestamps[start:end], as_of, side="left")
            )

        seen = self._history_items[start:end]
        positions = self._history_positions[start:end]
        return positions[positions >= 0], seen

    def _learned_path_serves(self, user_id: int, n_rows: int, seen: np.ndarray) -> bool:
        """Index membership first, then the routing policy — the sibling contract.

        A user with no representable history at this instant has no learned path
        to take under *either* policy, which is the same statement item-item
        makes with ``user_id not in self._user_to_index``. Above that, the
        history size is the distinct movie count, so it means here what it means
        in ``src/serving/recommendations.py`` and in ``routing.py``'s docstring.
        """
        if self._index is None or n_rows == 0 or user_id not in self._history_span:
            return False
        return routing.learned_path_serves(
            history_size=int(np.unique(seen).size),
            cold_start_threshold=self.cold_start_threshold,
        )

    def _profile(self, positions: np.ndarray) -> np.ndarray:
        """Unit-length mean of the watched items' normalized genre vectors.

        Accumulated in float64 from a float32 matrix: the genre vectors are
        small numbers and a long history is a long sum, and the profile decides
        a ranking whose ties are broken exactly. The final renormalization does
        not change the ordering for a given user — it is a positive scalar — but
        it puts the score in [0, 1], which is what lets a logged similarity be
        compared across users and against the era key without a scale in the way.
        """
        assert self._index is not None
        vectors = self._index.genre_vectors
        if positions.size == 0 or vectors.shape[1] == 0:
            return np.zeros(vectors.shape[1], dtype=np.float64)

        total = np.asarray(vectors[positions].sum(axis=0, dtype=np.float64))
        norm = float(np.linalg.norm(total))
        if norm == 0.0:
            return total
        return np.asarray(total / norm)

    def _rank(
        self, profile: np.ndarray, positions: np.ndarray, excluded: np.ndarray, k: int
    ) -> list[int]:
        """Order the whole catalog by (genre cosine, era proximity, movie id).

        The catalog rather than the interaction set: that difference is the rung.
        """
        assert self._index is not None
        index = self._index
        if index.n_items == 0 or k <= 0:
            return []

        # An explicit multiply-and-reduce rather than a matrix-vector product.
        # The genre space is twenty columns wide, so there is nothing to gain
        # from BLAS, and numpy's own pairwise reduction over a fixed shape is
        # reproducible in a way a threaded gemv is not promised to be — which
        # matters because these scores decide an exactly-broken ordering.
        similarity = (index.genre_vectors * profile).sum(axis=1)
        era_distance = self._era_distance(positions)

        # lexsort reads its keys last-to-first: genre similarity descending, then
        # the nearer era, then the lower movie id. The third key is what makes
        # the ordering total, and so the model reproducible down to the tie.
        order = np.lexsort((index.item_ids, era_distance, -similarity))

        # Seen items are dropped here rather than by masking the score arrays,
        # which is the post-filter shape item-item uses: the ordering is a
        # property of the catalog and the profile, and the exclusion is a
        # property of the user, so keeping them apart means the two cannot
        # silently interact.
        skip = set(excluded.tolist())
        # ``k + |seen|`` entries of the ordering are enough to yield k survivors
        # however unlucky the exclusions are — the same bound item-item asks
        # ``implicit`` for. Walking the whole 62 k-item ordering in Python
        # instead would cost more than the sort that produced it.
        limit = min(k + len(skip), index.n_items)
        out: list[int] = []
        for position in order[:limit]:
            item = int(index.item_ids[position])
            if item in skip:
                continue
            out.append(item)
            if len(out) == k:
                break
        return out

    def _era_distance(self, positions: np.ndarray) -> np.ndarray:
        """Years from each candidate to the *nearest* release year the user watched.

        Nearest rather than distance-to-the-mean because a mean invents an era
        nobody watched: a user of 1950s noir and 2015 blockbusters has a mean
        year around 1982, and ranking by closeness to 1982 would favour films
        from a decade they have shown no interest in. Nearest-watched answers the
        question the profile is actually asking — is this film close to something
        they chose? — and costs one ``searchsorted`` over a handful of distinct
        years.

        A candidate with no parseable year, or a user with no year in their
        history at all, gets infinity: last within its similarity block, never
        promoted by a year we do not have.
        """
        assert self._index is not None
        years = self._index.release_years
        distance = np.full(years.shape, np.inf, dtype=np.float64)

        watched = self._index.release_years[positions]
        watched = np.unique(watched[~np.isnan(watched)])
        if watched.size == 0:
            return distance

        known = ~np.isnan(years)
        if not known.any():
            return distance

        candidate_years = years[known]
        right = np.searchsorted(watched, candidate_years, side="left")
        below = watched[np.clip(right - 1, 0, watched.size - 1)]
        above = watched[np.clip(right, 0, watched.size - 1)]
        distance[known] = np.minimum(
            np.abs(candidate_years - below), np.abs(candidate_years - above)
        )
        return distance

    def _fallback(self, k: int, seen: np.ndarray) -> list[int]:
        """The popularity ranking minus what the user has already watched.

        Identical to ``PopularityModel.recommend`` when ``as_of`` is ``None`` —
        same ranking, same exclusions — and asserted so by a unit test. It is
        written out here rather than delegated because the model's own exclusion
        set is the strictly-prior one, and delegating would have an ``as_of``
        query filtered against a history that extends past the instant it is
        asking about. A fallback quietly consulting the future is exactly the
        kind of leak that never shows up as a failure.
        """
        excluded = set(seen.tolist())
        out: list[int] = []
        for item in self._popularity.ranking:
            if item in excluded:
                continue
            out.append(item)
            if len(out) == k:
                break
        return out
