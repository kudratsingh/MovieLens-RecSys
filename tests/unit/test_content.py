"""
Unit tests for the content-based cold-item retriever (ADR 0017, increment 1).

The model's claim is coverage, so the tests are weighted towards the properties
that make a coverage claim trustworthy rather than towards recommendation
quality — which this rung is explicit about not being able to demonstrate.
Four groups carry most of the weight:

  1. *Reach.* An item with no interaction at all must be scored and retrievable,
     because that is the entire reason the model exists. The negative half
     matters too: a genre-less item must be honestly unreachable rather than
     quietly promoted by a similarity we did not compute.
  2. *Point-in-time correctness.* Only interactions strictly before the query
     instant may inform a profile, so films sharing one second never help
     retrieve one another. MovieLens records whole seconds and CLAUDE.md
     singles this failure out; a leak here inflates every metric while leaving
     the model looking healthy.
  3. *Determinism.* Genre cosine over a twenty-value vocabulary ties in large
     blocks, so the tie-breaks decide most of the ordering. Both are asserted
     directly, and the fit is asserted to be invariant to the order rows arrive
     in.
  4. *Routing parity.* Cold users take the popularity fallback on exactly the
     rule every other candidate model applies, and ``was_served_by_content``
     cannot disagree with ``recommend`` about where that boundary sits.

Fixtures are built per test because each is a specific content structure, and a
shared catalog would make the assertions harder to read than the code.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD
from src.models.candidates.content import (
    ContentSimilarityModel,
    DuplicateCatalogItemError,
    build_item_content_index,
    parse_genres,
    parse_release_year,
)
from src.models.candidates.popularity import PopularityModel


def _movies(rows: list[tuple[int, str, str]]) -> pd.DataFrame:
    """(movieId, title, genres) triples — the three columns the index reads."""
    return pd.DataFrame(rows, columns=["movieId", "title", "genres"])


def _ratings(rows: list[tuple[int, int, int]]) -> pd.DataFrame:
    """(userId, movieId, timestamp) triples — the three columns the model reads."""
    return pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])


def _open_model() -> ContentSimilarityModel:
    """A model on the index-membership opt-out.

    Most fixtures below give a user two or three films, well under ADR 0001's
    threshold, so a model on the default would answer every one of them from its
    popularity fallback and the content logic would go untested. Where the
    *default* sends these users is asserted by the routing tests at the bottom
    of the file.
    """
    return ContentSimilarityModel(cold_start_threshold=None)


# ---- The representation (C-02) ------------------------------------------


def test_genres_parse_into_a_sorted_deduplicated_tuple() -> None:
    assert parse_genres("Drama|Action|Drama") == ("Action", "Drama")


@pytest.mark.parametrize("raw", ["", "(no genres listed)", "nan", None])
def test_absent_genres_parse_to_nothing(raw: object) -> None:
    # The sentinel must mean "no evidence", never a genre in its own right —
    # the same reading src/features/pipeline.py gives the column.
    assert parse_genres(raw) == ()


def test_the_release_year_comes_from_the_end_of_the_title() -> None:
    # A parenthesised year mid-title belongs to an alternate title, not to this
    # film; taking the first match would read the wrong year for some of them.
    assert parse_release_year("Cinema Paradiso (Nuovo cinema Paradiso) (1989)") == 1989
    assert parse_release_year("A Film With No Year") is None


def test_the_genre_vocabulary_is_derived_from_the_catalog() -> None:
    index = build_item_content_index(
        _movies(
            [
                (1, "A (2000)", "Drama"),
                (2, "B (2001)", "Action|Drama"),
                (3, "C (2002)", "(no genres listed)"),
            ]
        )
    )
    # Three films, two genres — not MovieLens's twenty. A hardcoded vocabulary
    # would make this a twenty-dimensional space and the assertions unreadable.
    assert index.genres == ("Action", "Drama")
    assert index.n_items == 3


def test_each_item_contributes_one_unit_of_genre_mass() -> None:
    index = build_item_content_index(
        _movies([(1, "A (2000)", "Drama"), (2, "B (2001)", "Action|Drama")])
    )
    norms = np.linalg.norm(index.genre_vectors, axis=1)
    # A two-genre film is not twice the evidence of a one-genre film; both are
    # unit length, which is the accounting user_genre_affinity uses too.
    assert norms == pytest.approx([1.0, 1.0])


def test_a_genreless_item_is_an_all_zero_row() -> None:
    index = build_item_content_index(
        _movies([(1, "A (2000)", "Drama"), (2, "B (2001)", "(no genres listed)")])
    )
    assert not index.genre_vectors[1].any()
    assert index.has_genres.tolist() == [True, False]


def test_a_missing_release_year_is_nan_rather_than_a_guess() -> None:
    index = build_item_content_index(
        _movies([(1, "A (2000)", "Drama"), (2, "No Year Here", "Drama")])
    )
    assert index.release_years[0] == 2000.0
    assert np.isnan(index.release_years[1])
    assert index.has_release_year.tolist() == [True, False]


def test_an_id_outside_the_catalog_maps_to_the_sentinel() -> None:
    index = build_item_content_index(_movies([(1, "A (2000)", "Drama"), (5, "B (2001)", "Drama")]))
    assert index.positions_for(np.array([5, 1, 99], dtype=np.int64)).tolist() == [1, 0, -1]


def test_a_duplicated_catalog_row_is_refused() -> None:
    # movies.movieId is a primary key; a duplicate means the frame is not the
    # table this model is specified against, and picking one row would give the
    # item a content vector nobody chose.
    with pytest.raises(DuplicateCatalogItemError, match="duplicated movieId"):
        build_item_content_index(
            _movies([(1, "A (2000)", "Drama"), (1, "A prime (2000)", "Action")])
        )


def test_a_catalog_missing_a_required_column_is_refused() -> None:
    with pytest.raises(KeyError, match="title"):
        build_item_content_index(pd.DataFrame({"movieId": [1], "genres": ["Drama"]}))


# ---- Reach: the cold item, which is the whole point ---------------------


def test_an_item_with_no_interactions_is_retrievable() -> None:
    # 300 has never been rated by anyone, so no co-occurrence index contains it.
    # Here it is scored like any other Drama and beats the Horror film.
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (200, "Rated By Nobody Else (2001)", "Horror"),
            (300, "Cold Drama (2000)", "Drama"),
        ]
    )
    train = _ratings([(1, 100, 10), (2, 200, 10)])
    model = _open_model().fit(train, movies)

    assert model.cold_item_ids.tolist() == [300]
    assert model.recommend(user_id=1, k=2) == [300, 200]


def test_coverage_counts_the_cold_population_and_what_it_can_serve() -> None:
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (300, "Cold With Genres (1999)", "Drama"),
            (301, "Cold Without Genres (1998)", "(no genres listed)"),
            (302, "Cold Without A Year", "Comedy"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    coverage = model.coverage

    assert coverage.n_catalog_items == 4
    assert coverage.n_interaction_items == 1
    assert coverage.n_cold_items == 3
    assert coverage.n_cold_items_with_genres == 2
    # The population increment 1 cannot serve, stated as its own number.
    assert coverage.n_cold_items_without_genres == 1
    assert coverage.n_cold_items_with_release_year == 2
    assert coverage.n_history_rows_outside_catalog == 0


def test_a_genreless_item_ranks_below_every_item_with_genre_overlap() -> None:
    # It carries no genre evidence either way, so it lands in the zero-overlap
    # block rather than being promoted. At k=500 out of 62k that block is never
    # reached, which is exactly why ADR 0017 says this increment cannot serve
    # these items.
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (200, "Also Drama (2000)", "Drama"),
            (300, "No Genres (2000)", "(no genres listed)"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    assert model.recommend(user_id=1, k=5) == [200, 300]


def test_a_history_row_outside_the_catalog_is_counted_not_ignored() -> None:
    model = _open_model().fit(
        _ratings([(1, 100, 10), (1, 999, 11)]), _movies([(100, "A (2000)", "Drama")])
    )
    assert model.coverage.n_history_rows_outside_catalog == 1


# ---- Scoring and its tie-breaks -----------------------------------------


def test_genre_overlap_outranks_no_overlap() -> None:
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (200, "Same Genre (2000)", "Drama"),
            (300, "Different Genre (2000)", "Horror"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    assert model.recommend(user_id=1, k=5) == [200, 300]


def test_a_focused_match_outranks_a_diluted_one() -> None:
    # Both share Drama with the profile, but the second spends half its genre
    # mass elsewhere. Cosine says the pure Drama is the closer film, which is
    # the graded answer a plain set-intersection feature cannot give.
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (200, "Pure Drama (2000)", "Drama"),
            (300, "Drama And Horror (2000)", "Drama|Horror"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    assert model.recommend(user_id=1, k=5) == [200, 300]


def test_the_era_key_breaks_a_genre_tie() -> None:
    movies = _movies(
        [
            (100, "Watched (1960)", "Drama"),
            (200, "Same Era (1961)", "Drama"),
            (300, "Distant Era (2015)", "Drama"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    assert model.recommend(user_id=1, k=5) == [200, 300]


def test_era_distance_is_to_the_nearest_watched_year_not_the_mean() -> None:
    # A user of 1950s noir and 2015 blockbusters has a mean year near 1982. A
    # mean-based prior would rank the 1982 film first; nearest-watched ranks the
    # 2014 film first, which is the era the user actually chose.
    movies = _movies(
        [
            (100, "Noir (1950)", "Drama"),
            (101, "Blockbuster (2015)", "Drama"),
            (200, "Near The Mean (1982)", "Drama"),
            (300, "Near A Watched Year (2014)", "Drama"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10), (1, 101, 11)]), movies)
    assert model.recommend(user_id=1, k=2) == [300, 200]


def test_an_item_with_no_year_sorts_last_within_its_similarity_block() -> None:
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (200, "Dated (1900)", "Drama"),
            (300, "Undated Drama", "Drama"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    # A century away still beats a year we simply do not have: an absent
    # attribute must never promote an item.
    assert model.recommend(user_id=1, k=5) == [200, 300]


def test_the_movie_id_breaks_a_genre_and_era_tie() -> None:
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (300, "Identical Content (2000)", "Drama"),
            (200, "Identical Content Too (2000)", "Drama"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    # Two films indistinguishable on both content keys still have a defined
    # order, which is what makes the model reproducible down to the tie.
    assert model.recommend(user_id=1, k=5) == [200, 300]


def test_no_popularity_term_reaches_the_ranking() -> None:
    # 200 is the most-rated film in train and 300 has never been rated. They are
    # content-identical, so the id tie-break decides and the cold item wins.
    # A popularity tie-break — which last_item.py deliberately uses — would lose
    # every cold item every tie, which is why this model has none.
    movies = _movies(
        [
            (100, "Watched (2000)", "Drama"),
            (300, "Cold (2000)", "Drama"),
            (400, "Popular (2000)", "Drama"),
        ]
    )
    train = _ratings([(1, 100, 10), (2, 400, 10), (3, 400, 11), (4, 400, 12)])
    model = _open_model().fit(train, movies)
    assert model.recommend(user_id=1, k=1) == [300]


# ---- Point-in-time correctness ------------------------------------------


def test_items_sharing_a_timestamp_never_inform_each_other() -> None:
    # User 1 watched a Comedy, then a Drama and a Horror in the same second.
    # Asked at exactly that second, neither of the tied pair may be in the
    # profile — otherwise each would be helping to retrieve the other's genre
    # neighbours, which is the sequence-construction leakage CLAUDE.md singles
    # out. MovieLens records whole seconds, so this pair is not a contrivance.
    movies = _movies(
        [
            (100, "Comedy (2000)", "Comedy"),
            (110, "Drama (2000)", "Drama"),
            (111, "Horror (2000)", "Horror"),
            (200, "Other Comedy (2000)", "Comedy"),
            (201, "Other Drama (2000)", "Drama"),
            (202, "Other Horror (2000)", "Horror"),
        ]
    )
    tied = _ratings([(1, 100, 10), (1, 110, 50), (1, 111, 50)])
    model = _open_model().fit(tied, movies)
    index = build_item_content_index(movies)

    profile = model.content_profile(user_id=1, as_of=50)
    assert profile is not None
    assert profile[index.genres.index("Drama")] == 0.0
    assert profile[index.genres.index("Horror")] == 0.0
    assert profile[index.genres.index("Comedy")] > 0.0

    # The sharpest form of the claim: at that instant the model must behave
    # exactly as if the tied pair had never been recorded.
    without_the_pair = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    assert model.recommend(user_id=1, k=3, as_of=50) == without_the_pair.recommend(
        user_id=1, k=3, as_of=50
    )

    # One second later both are in scope, and all three genres are taste.
    later = model.content_profile(user_id=1, as_of=51)
    assert later is not None
    assert later[index.genres.index("Drama")] > 0.0


def test_only_the_strictly_earlier_history_informs_a_profile() -> None:
    movies = _movies(
        [
            (100, "Early Drama (2000)", "Drama"),
            (101, "Later Horror (2000)", "Horror"),
            (200, "Other Drama (2000)", "Drama"),
            (201, "Other Horror (2000)", "Horror"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10), (1, 101, 20)]), movies)
    horror = build_item_content_index(movies).genres.index("Horror")

    at_15 = model.content_profile(user_id=1, as_of=15)
    at_25 = model.content_profile(user_id=1, as_of=25)
    assert at_15 is not None and at_25 is not None
    # At t=15 the Horror film has not happened yet, so Horror is not taste.
    assert at_15[horror] == 0.0
    assert at_25[horror] > 0.0
    # And the Drama neighbour still leads the slate at t=15, where Drama is the
    # only thing the user has shown.
    assert model.recommend(user_id=1, k=1, as_of=15) == [200]


def test_the_seen_set_is_also_cut_strictly_before_the_query() -> None:
    # An item consumed at exactly ``as_of`` is not yet seen at prediction time,
    # so it stays a candidate. The exclusion filter and the profile must read
    # the same instant, or the model would be hiding an item on the strength of
    # information it is not allowed to have.
    movies = _movies([(100, "A (2000)", "Drama"), (200, "B (2000)", "Drama")])
    model = _open_model().fit(_ratings([(1, 100, 10), (1, 200, 20)]), movies)
    assert 200 in model.recommend(user_id=1, k=5, as_of=20)
    assert 200 not in model.recommend(user_id=1, k=5, as_of=21)


def test_nothing_after_the_split_cutoff_reaches_the_model() -> None:
    # The frame-level half of point-in-time correctness: the caller hands over
    # the train slice, and holdout interactions are simply not in it.
    movies = _movies(
        [
            (100, "Drama (2000)", "Drama"),
            (200, "Horror (2000)", "Horror"),
            (201, "Other Horror (2000)", "Horror"),
        ]
    )
    ratings = pd.DataFrame(
        {
            "userId": [1] * 5 + [2] * 5,
            "movieId": [100, 100, 100, 100, 200] + [100] * 5,
            "timestamp": [1, 2, 3, 4, 5_000_000] + [1, 2, 3, 4, 5],
            "rating": [4.0] * 10,
        }
    )
    split = temporal_split(ratings)
    model = _open_model().fit(split.train, movies)

    # User 1's Horror rating is in the holdout, so Horror must not be taste and
    # the other Horror film must not be promoted by it.
    assert model.recommend(user_id=1, k=1) == [200]
    profile = model.content_profile(user_id=1)
    assert profile is not None
    assert profile[build_item_content_index(movies).genres.index("Horror")] == 0.0


def test_seen_items_are_excluded() -> None:
    movies = _movies([(100, "A (2000)", "Drama"), (200, "B (2000)", "Drama")])
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    assert model.recommend(user_id=1, k=5) == [200]
    assert model.recommend(user_id=1, k=5, filter_seen=False) == [100, 200]


# ---- Determinism ---------------------------------------------------------


def test_fit_is_invariant_to_the_order_rows_arrive_in() -> None:
    movies = _movies(
        [
            (100, "A (2000)", "Drama"),
            (101, "B (1990)", "Action|Drama"),
            (102, "C (1980)", "Comedy"),
            (200, "D (1995)", "Drama"),
            (201, "E (1985)", "Comedy|Drama"),
        ]
    )
    rows = [(1, 100, 10), (1, 101, 10), (1, 102, 11)]
    forward = _open_model().fit(_ratings(rows), movies)
    backward = _open_model().fit(_ratings(list(reversed(rows))), movies)
    # Equal to the bit, not merely equal in ranking: the profile is a float sum
    # over the history, and float addition is not associative.
    assert np.array_equal(forward.content_profile(1), backward.content_profile(1))  # type: ignore[arg-type]
    assert forward.recommend(1, k=5) == backward.recommend(1, k=5)


def test_a_catalog_in_a_different_order_produces_the_same_index() -> None:
    rows = [(1, "A (2000)", "Drama"), (5, "B (1990)", "Action"), (3, "C (1980)", "Comedy")]
    forward = build_item_content_index(_movies(rows))
    backward = build_item_content_index(_movies(list(reversed(rows))))
    assert forward.item_ids.tolist() == backward.item_ids.tolist() == [1, 3, 5]
    assert np.array_equal(forward.genre_vectors, backward.genre_vectors)


def test_repeated_recommendations_are_identical() -> None:
    movies = _movies([(100, "A (2000)", "Drama"), (200, "B (2000)", "Drama")])
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    assert model.recommend(1, k=5) == model.recommend(1, k=5)


# ---- Degenerate inputs ---------------------------------------------------


def test_a_profile_of_only_genreless_films_still_orders_deterministically() -> None:
    # Essentially unreachable on MovieLens — it needs every one of a user's
    # films to carry no genre — but it must not be undefined behaviour. The
    # genre key is constant, so era proximity and then movie id decide, and the
    # trainer counts these users because a weak answer should be visible.
    movies = _movies(
        [
            (100, "Watched (1970)", "(no genres listed)"),
            (200, "Near (1971)", "Drama"),
            (300, "Far (2015)", "Drama"),
        ]
    )
    model = _open_model().fit(_ratings([(1, 100, 10)]), movies)
    profile = model.content_profile(1)
    assert profile is not None and not profile.any()
    assert model.recommend(1, k=5) == [200, 300]


def test_an_empty_train_frame_produces_an_empty_model() -> None:
    model = _open_model().fit(
        _ratings([]).astype({"userId": int, "movieId": int, "timestamp": int}),
        _movies([(100, "A (2000)", "Drama")]),
    )
    assert model.recommend(1, k=5) == []
    assert model.coverage.n_cold_items == 1


def test_an_empty_catalog_produces_an_empty_model() -> None:
    model = _open_model().fit(_ratings([(1, 100, 10)]), _movies([]))
    assert model.recommend(1, k=5) == []
    assert model.coverage.n_catalog_items == 0


def test_a_train_frame_missing_a_required_column_is_refused() -> None:
    with pytest.raises(KeyError, match="timestamp"):
        _open_model().fit(
            pd.DataFrame({"userId": [1], "movieId": [100]}), _movies([(100, "A (2000)", "Drama")])
        )


# ---- Routing parity ------------------------------------------------------


def _threshold_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A catalog plus a warm user at the threshold and a one-film cold user."""
    movies = _movies(
        [(movie_id, f"Film {movie_id} (2000)", "Drama") for movie_id in range(100, 130)]
    )
    warm = [(1, 100 + offset, 10 + offset) for offset in range(COLD_START_THRESHOLD)]
    cold = [(2, 120, 10)]
    return _ratings(warm + cold), movies


def test_a_user_below_the_threshold_takes_the_popularity_fallback() -> None:
    train, movies = _threshold_fixture()
    model = ContentSimilarityModel().fit(train, movies)
    assert model.was_served_by_content(2) is False
    assert model.recommend(2, k=5) == PopularityModel().fit(train).recommend(2, k=5)


def test_a_user_at_the_threshold_is_served_by_content() -> None:
    train, movies = _threshold_fixture()
    model = ContentSimilarityModel().fit(train, movies)
    assert model.was_served_by_content(1) is True


def test_the_predicate_and_recommend_cannot_disagree() -> None:
    train, movies = _threshold_fixture()
    model = ContentSimilarityModel().fit(train, movies)
    popularity = PopularityModel().fit(train)
    for user_id in (1, 2):
        served = model.was_served_by_content(user_id)
        matches_fallback = model.recommend(user_id, k=5) == popularity.recommend(user_id, k=5)
        # The fallback and the content path can coincide by luck on a tiny
        # fixture, so this asserts the direction that cannot: a user the
        # predicate calls cold must be answered by popularity.
        assert served or matches_fallback


def test_an_unknown_user_takes_the_popularity_fallback() -> None:
    train, movies = _threshold_fixture()
    model = ContentSimilarityModel().fit(train, movies)
    assert model.was_served_by_content(9999) is False
    assert model.content_profile(9999) is None
    assert model.recommend(9999, k=3) == PopularityModel().fit(train).recommend(9999, k=3)


def test_the_index_membership_opt_out_serves_a_one_interaction_user() -> None:
    train, movies = _threshold_fixture()
    model = ContentSimilarityModel(cold_start_threshold=None).fit(train, movies)
    assert model.was_served_by_content(2) is True


def test_the_routing_predicate_reads_the_history_at_the_query_instant() -> None:
    # A user who is warm today was cold before their tenth film. The predicate
    # takes the same as_of recommend does, so the two cannot disagree about a
    # point-in-time query the way a whole-history predicate would.
    train, movies = _threshold_fixture()
    model = ContentSimilarityModel().fit(train, movies)
    assert model.was_served_by_content(1, as_of=15) is False
    assert model.was_served_by_content(1) is True


def test_the_fallback_matches_the_popularity_model_it_embeds() -> None:
    # The fallback walk is written out rather than delegated, so that an as_of
    # query filters against the strictly-prior history rather than the whole of
    # it. At as_of=None the two must still be the same answer, or the model has
    # quietly acquired a second cold-start policy.
    train, movies = _threshold_fixture()
    model = ContentSimilarityModel().fit(train, movies)
    popularity = PopularityModel().fit(train)
    assert model.recommend(2, k=10) == popularity.recommend(2, k=10)
