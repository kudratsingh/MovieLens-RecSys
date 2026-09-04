"""ADR 0011's cold-start cohort: generation, provenance, loading, and scoring.

Everything here runs on a small in-memory fixture rather than on MovieLens,
because CI has no dataset — which is also the honest constraint, since a
determinism claim that can only be checked on a 1 GB download is a claim nobody
checks. What the fixture cannot prove (that the *real* v1 parquet regenerates
identically) is what `make synth-cold-cohort` plus a clean `dvc status` proves
on a machine that has the data.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, evaluate
from synthetic.cold_start import harness
from synthetic.cold_start.config import (
    BUCKET_ID_STRIDE,
    COHORT_HISTORY_ROWS,
    COHORT_SIZE,
    HISTORY_BUCKETS,
    HISTORY_ROLE,
    MOVIELENS_DVC_PATH,
    SYNTH_COLD_RATING,
    SYNTH_COLD_SEED,
    SYNTH_COLD_TENANT_ID,
    SYNTH_COLD_USER_ID_BASE,
    TARGET_ROLE,
    TIMESTAMP_OFFSET_SECONDS,
    USERS_PER_BUCKET,
    user_ids_for_bucket,
)
from synthetic.cold_start.generator import (
    CohortGenerationError,
    build_provenance,
    cohort_fingerprint,
    generate_cohort,
    item_popularity,
    main,
    read_movielens_data_version,
    write_cohort,
)
from synthetic.cold_start.load import (
    CohortDataVersionMismatchError,
    load_cohort,
    load_cohort_if_present,
)

# MovieLens 25M's own user id range, and the demo personas, from
# synthetic/personas/personas.json. Repeated here on purpose: the point of the
# test is that the cohort's range is disjoint from *these specific numbers*, so
# importing them from the thing under test would prove nothing.
MOVIELENS_USER_IDS = (1, 162_541)
DEMO_PERSONA_USER_IDS = (900000101, 900000102, 900000103, 900000104)

CUTOFF = 1_600_000_000
CATALOG_SIZE = 40
TEST_BUCKETS = (0, 1, 3, 10)
TEST_USERS_PER_BUCKET = 6
DATA_VERSION = "0123456789abcdef0123456789abcdef.dir"


def _train_ratings() -> pd.DataFrame:
    """A tiny train slice with a deliberately Zipf-ish popularity profile.

    Item ``i`` gets roughly ``400 / i`` interactions, so the head is two orders
    of magnitude more popular than the tail — enough separation for the
    popularity-weighting assertion below to be a real test rather than a
    coin flip.
    """
    movie_ids: list[int] = []
    for item in range(1, CATALOG_SIZE + 1):
        movie_ids.extend([item] * max(2, 400 // item))
    return pd.DataFrame(
        {
            "userId": [1_000 + index % 97 for index in range(len(movie_ids))],
            "movieId": movie_ids,
            "rating": [3.5] * len(movie_ids),
            "timestamp": [CUTOFF - 10_000] * len(movie_ids),
        }
    )


def _cohort_frame(seed: int = SYNTH_COLD_SEED) -> pd.DataFrame:
    return generate_cohort(
        _train_ratings(),
        CUTOFF,
        seed=seed,
        buckets=TEST_BUCKETS,
        users_per_bucket=TEST_USERS_PER_BUCKET,
    )


def _timestamped_ratings() -> pd.DataFrame:
    """`_train_ratings` with a spread of timestamps, for tests that need a split.

    The rows are shuffled on a fixed seed before the timestamps are assigned so
    that the train side of `temporal_split` holds most of the catalog. Assigning
    them in catalog order would put the whole head of the popularity curve in
    train and the tail in holdout, which is both unrepresentative and — with a
    bucket that draws eleven distinct items — close to the edge of usable.
    """
    ratings = _train_ratings().sample(frac=1.0, random_state=0).reset_index(drop=True)
    ratings["timestamp"] = [CUTOFF - 100_000 + index for index in range(len(ratings))]
    return ratings


def _write(frame: pd.DataFrame, path: Path, *, data_version: str = DATA_VERSION) -> None:
    write_cohort(
        frame,
        path,
        provenance=build_provenance(
            frame,
            cutoff=CUTOFF,
            seed=SYNTH_COLD_SEED,
            buckets=TEST_BUCKETS,
            users_per_bucket=TEST_USERS_PER_BUCKET,
            data_version=data_version,
        ),
    )


# --- config ------------------------------------------------------------------


def test_the_cohort_is_the_size_adr_0011_specifies() -> None:
    assert HISTORY_BUCKETS == (0, 1, 3, 10)
    assert USERS_PER_BUCKET == 500
    assert COHORT_SIZE == 2_000
    # 0+1+3+10 interactions x 500 users. The number matters because the whole
    # "this cannot move the real metrics" argument rests on its size.
    assert COHORT_HISTORY_ROWS == 7_000


def test_the_buckets_straddle_the_cold_start_threshold() -> None:
    below = [bucket for bucket in HISTORY_BUCKETS if bucket < COLD_START_THRESHOLD]
    at_or_above = [bucket for bucket in HISTORY_BUCKETS if bucket >= COLD_START_THRESHOLD]
    assert below == [0, 1, 3]
    assert at_or_above == [10]


def test_user_ids_cannot_collide_with_movielens_or_the_demo_personas() -> None:
    all_ids = [user_id for bucket in HISTORY_BUCKETS for user_id in user_ids_for_bucket(bucket)]
    assert len(set(all_ids)) == COHORT_SIZE
    assert min(all_ids) > MOVIELENS_USER_IDS[1]
    assert min(all_ids) > max(DEMO_PERSONA_USER_IDS)
    assert not set(all_ids) & set(DEMO_PERSONA_USER_IDS)


def test_the_bucket_stride_leaves_no_room_for_two_buckets_to_overlap() -> None:
    # The id encodes its bucket only while a bucket's users fit inside one
    # stride; this is the invariant that keeps that true if either number moves.
    assert USERS_PER_BUCKET < BUCKET_ID_STRIDE
    assert user_ids_for_bucket(0)[0] == SYNTH_COLD_USER_ID_BASE


# --- generation --------------------------------------------------------------


def test_every_bucket_has_the_requested_users_with_the_requested_history() -> None:
    frame = _cohort_frame()
    history = frame[frame["role"] == HISTORY_ROLE]

    for bucket in TEST_BUCKETS:
        users = frame.loc[frame["history_size"] == bucket, "userId"].unique()
        assert len(users) == TEST_USERS_PER_BUCKET
        for user_id in users:
            assert len(history[history["userId"] == user_id]) == bucket

    targets = frame[frame["role"] == TARGET_ROLE]
    assert len(targets) == TEST_USERS_PER_BUCKET * len(TEST_BUCKETS)
    assert targets["userId"].is_unique


def test_a_target_is_never_in_its_own_users_history() -> None:
    frame = _cohort_frame()
    history_pairs = {
        (int(row.userId), int(row.movieId))
        for row in frame[frame["role"] == HISTORY_ROLE].itertuples(index=False)
    }
    for row in frame[frame["role"] == TARGET_ROLE].itertuples(index=False):
        assert (int(row.userId), int(row.movieId)) not in history_pairs


def test_a_users_history_has_no_duplicates() -> None:
    frame = _cohort_frame()
    history = frame[frame["role"] == HISTORY_ROLE]
    assert not history.duplicated(subset=["userId", "movieId"]).any()


def test_every_row_is_anchored_24_hours_before_the_cutoff() -> None:
    frame = _cohort_frame()
    assert (frame["timestamp"] == CUTOFF - TIMESTAMP_OFFSET_SECONDS).all()
    assert TIMESTAMP_OFFSET_SECONDS == 86_400


def test_rows_carry_the_tenant_and_the_synthetic_flag() -> None:
    frame = _cohort_frame()
    assert (frame["tenant_id"] == SYNTH_COLD_TENANT_ID).all()
    assert frame["synthetic"].all()
    # ADR 0002 drops the value before it reaches a model, but the column has to
    # exist and has to be a rating a real row could carry.
    assert (frame["rating"] == SYNTH_COLD_RATING).all()
    assert 0.5 <= SYNTH_COLD_RATING <= 5.0


def test_history_is_popularity_weighted_rather_than_uniform() -> None:
    """The drawn items should skew hard toward the head of the catalog.

    Comparing the mean interaction count of drawn items against the catalog's
    own mean is the cheapest statement of "not uniform" that would actually
    fail if the weights were dropped.
    """
    items, weights = item_popularity(_train_ratings())
    popularity = dict(zip(items.tolist(), weights.tolist(), strict=True))
    catalog_mean = float(weights.mean())

    frame = _cohort_frame()
    drawn_mean = sum(popularity[int(movie_id)] for movie_id in frame["movieId"]) / len(frame)

    assert drawn_mean > catalog_mean * 2

    # And the single most popular item should appear far more often than the
    # least popular one, which uniform sampling would make equally likely.
    counts = frame["movieId"].value_counts()
    assert counts.get(1, 0) > counts.get(CATALOG_SIZE, 0)


def test_the_target_distribution_does_not_drift_across_buckets() -> None:
    """Targets are the *first* draw, so bucket 10's is no harder than bucket 0's.

    Taking the last of ``n+1`` draws instead would hand the deeper buckets a
    systematically less popular target and confound the per-bucket comparison
    the cohort exists to make. A wide cohort makes the means comparable.
    """
    frame = generate_cohort(_train_ratings(), CUTOFF, buckets=TEST_BUCKETS, users_per_bucket=400)
    items, weights = item_popularity(_train_ratings())
    popularity = dict(zip(items.tolist(), weights.tolist(), strict=True))

    targets = frame[frame["role"] == TARGET_ROLE]
    means = {
        bucket: sum(
            popularity[int(movie_id)]
            for movie_id in targets.loc[targets["history_size"] == bucket, "movieId"]
        )
        / 400
        for bucket in TEST_BUCKETS
    }
    # Sampling noise at n=400 is real; a rank-ordering bias would be much larger
    # than this band, since bucket 10's target would be the 11th-ranked draw.
    assert max(means.values()) < min(means.values()) * 1.35


def test_a_bucket_deeper_than_the_catalog_is_refused() -> None:
    with pytest.raises(CohortGenerationError, match="fewer than"):
        generate_cohort(_train_ratings(), CUTOFF, buckets=(CATALOG_SIZE,), users_per_bucket=1)


def test_an_empty_train_slice_is_refused() -> None:
    with pytest.raises(CohortGenerationError, match="empty train slice"):
        generate_cohort(_train_ratings().iloc[0:0], CUTOFF)


def test_a_shape_that_would_mint_colliding_user_ids_is_refused() -> None:
    """Both ways the id scheme can break, refused rather than silently aliased."""
    with pytest.raises(CohortGenerationError, match="must be distinct"):
        generate_cohort(_train_ratings(), CUTOFF, buckets=(1, 1), users_per_bucket=2)

    with pytest.raises(CohortGenerationError, match="id stride"):
        generate_cohort(
            _train_ratings(), CUTOFF, buckets=(0, 1), users_per_bucket=BUCKET_ID_STRIDE + 1
        )


# --- determinism -------------------------------------------------------------


def test_two_generations_from_the_same_seed_are_row_identical() -> None:
    assert _cohort_frame().equals(_cohort_frame())


def test_two_writes_from_the_same_seed_are_byte_identical(tmp_path: Path) -> None:
    first, second = tmp_path / "a.parquet", tmp_path / "b.parquet"
    _write(_cohort_frame(), first)
    _write(_cohort_frame(), second)
    assert first.read_bytes() == second.read_bytes()


def test_the_fingerprint_is_pinned() -> None:
    """A regression here means the generator changed, not the writer.

    The parquet bytes move with pyarrow; this hash moves only with the rows,
    which is what non-negotiable #5 actually cares about. If numpy ever changes
    its uniform stream under us, this is the test that says so.
    """
    assert (
        cohort_fingerprint(_cohort_frame())
        == "dfd83a233311d8d3e67012bdff4383f61da29d1081abc74f44e3c3090e26ad86"
    )


def test_a_different_seed_produces_a_different_cohort() -> None:
    assert cohort_fingerprint(_cohort_frame()) != cohort_fingerprint(_cohort_frame(seed=43))


# --- provenance and loading --------------------------------------------------


def test_the_committed_dvc_pointer_yields_one_data_version() -> None:
    version = read_movielens_data_version()
    assert re.fullmatch(r"[0-9a-f]{32}\.dir", version), version
    assert version in MOVIELENS_DVC_PATH.read_text(encoding="utf-8")


def test_the_parquet_carries_its_provenance(tmp_path: Path) -> None:
    path = tmp_path / "users.parquet"
    frame = _cohort_frame()
    _write(frame, path)

    metadata = pq.read_table(path).schema.metadata or {}
    assert metadata[b"synth_cold_seed"] == str(SYNTH_COLD_SEED).encode()
    assert metadata[b"synth_cold_split_cutoff"] == str(CUTOFF).encode()
    assert metadata[b"synth_cold_data_version"] == DATA_VERSION.encode()
    assert metadata[b"synth_cold_fingerprint"] == cohort_fingerprint(frame).encode()
    # Table.from_pandas' own metadata blob is dropped, which is part of what
    # makes the file byte-stable.
    assert b"pandas" not in metadata


def test_loading_splits_history_from_targets(tmp_path: Path) -> None:
    path = tmp_path / "users.parquet"
    _write(_cohort_frame(), path)

    cohort = load_cohort(path, expected_data_version=DATA_VERSION)

    assert list(cohort.history.columns) == ["userId", "movieId", "rating", "timestamp"]
    assert len(cohort.history) == TEST_USERS_PER_BUCKET * sum(TEST_BUCKETS)
    assert cohort.buckets == TEST_BUCKETS
    assert cohort.n_users == TEST_USERS_PER_BUCKET * len(TEST_BUCKETS)
    for bucket, users in cohort.targets_by_bucket.items():
        assert len(users) == TEST_USERS_PER_BUCKET
        assert all(len(targets) == 1 for targets in users.values())
        assert set(users) == set(user_ids_for_bucket(bucket, count=TEST_USERS_PER_BUCKET))

    # The history the trainer will fit on holds no target, which is the whole
    # reason the loader hands the two out separately.
    history_pairs = set(zip(cohort.history["userId"], cohort.history["movieId"], strict=True))
    for users in cohort.targets_by_bucket.values():
        for user_id, targets in users.items():
            assert not {(user_id, target) for target in targets} & history_pairs


def test_a_cohort_from_a_different_dataset_fails_loud(tmp_path: Path) -> None:
    path = tmp_path / "users.parquet"
    _write(_cohort_frame(), path, data_version="deadbeef" * 4 + ".dir")

    with pytest.raises(CohortDataVersionMismatchError, match="was generated against"):
        load_cohort(path, expected_data_version=DATA_VERSION)


def test_a_data_version_mismatch_is_not_an_optional_extra(tmp_path: Path) -> None:
    """``load_cohort_if_present`` tolerates absence, never disagreement."""
    path = tmp_path / "users.parquet"
    _write(_cohort_frame(), path, data_version="deadbeef" * 4 + ".dir")

    with pytest.raises(CohortDataVersionMismatchError):
        load_cohort_if_present(path, expected_data_version=DATA_VERSION)


def test_an_absent_parquet_is_not_an_error(tmp_path: Path) -> None:
    assert load_cohort_if_present(tmp_path / "nothing-here.parquet") is None


# --- the CLI -----------------------------------------------------------------


def test_the_cli_generates_from_a_ratings_csv(tmp_path: Path) -> None:
    """The `--ratings-csv` path a machine without a loaded Postgres uses."""
    csv = tmp_path / "ratings.csv"
    _timestamped_ratings().to_csv(csv, index=False)

    out = tmp_path / "users.parquet"
    assert main(["--out", str(out), "--ratings-csv", str(csv)]) == 0

    cohort = load_cohort(out)
    assert cohort.n_users == COHORT_SIZE
    assert len(cohort.history) == COHORT_HISTORY_ROWS
    assert cohort.provenance.data_version == read_movielens_data_version()
    assert cohort.provenance.seed == SYNTH_COLD_SEED


# --- the eval harness --------------------------------------------------------


def _targets(users_per_bucket: int = 2) -> dict[int, dict[int, set[int]]]:
    return {
        bucket: {
            user_id: {900_000 + index}
            for index, user_id in enumerate(user_ids_for_bucket(bucket, count=users_per_bucket))
        }
        for bucket in TEST_BUCKETS
    }


def test_evaluate_without_a_cohort_is_exactly_what_it_was() -> None:
    result = evaluate({1: [100]}, {1: {100}}, {1: 50})
    assert result.synthetic_cold_slices == {}
    assert result.overall.recall == pytest.approx(1.0)


def test_evaluate_populates_one_slice_per_bucket() -> None:
    targets = _targets()
    # Every synthetic user gets their target back; every bucket scores 1.0.
    recommendations = {
        user_id: list(target) for users in targets.values() for user_id, target in users.items()
    }

    result = evaluate(recommendations, {}, {}, k=K_CANDIDATES, synthetic_cold_users=targets)

    assert set(result.synthetic_cold_slices) == set(TEST_BUCKETS)
    for bucket, slice_ in result.synthetic_cold_slices.items():
        assert slice_.history_size == bucket
        assert slice_.n_users == 2
        assert slice_.metrics.recall == pytest.approx(1.0)
        # No predicate was supplied: unmeasured, not zero.
        assert slice_.n_fallback_served is None


def test_synthetic_users_never_enter_the_warm_or_cold_slices() -> None:
    targets = _targets()
    recommendations: dict[int, list[int]] = {1: [100]}
    recommendations.update({user_id: [999] for users in targets.values() for user_id in users})

    with_cohort = evaluate(recommendations, {1: {100}}, {1: 50}, synthetic_cold_users=targets)
    without = evaluate(recommendations, {1: {100}}, {1: 50})

    assert (with_cohort.warm, with_cohort.cold, with_cohort.overall) == (
        without.warm,
        without.cold,
        without.overall,
    )
    assert with_cohort.n_warm_users == without.n_warm_users == 1
    assert with_cohort.n_cold_users == without.n_cold_users == 0


def test_the_routing_predicate_is_counted_per_bucket() -> None:
    targets = _targets()
    learned_ids = set(user_ids_for_bucket(10, count=2))

    result = evaluate(
        {},
        {},
        {},
        synthetic_cold_users=targets,
        synthetic_cold_served_by=lambda user_id: user_id in learned_ids,
    )

    assert [result.synthetic_cold_slices[bucket].n_fallback_served for bucket in TEST_BUCKETS] == [
        2,
        2,
        2,
        0,
    ]
    assert harness.routing_is_correct(result) is True


def test_a_model_that_routes_at_the_wrong_boundary_is_reported_as_wrong() -> None:
    targets = _targets()
    # "Any history at all is warm" — the boundary the offline candidate models
    # actually use, which is not the one ADR 0001 specifies.
    any_history = {
        user_id
        for bucket in TEST_BUCKETS
        if bucket > 0
        for user_id in user_ids_for_bucket(bucket, count=2)
    }

    result = evaluate(
        {},
        {},
        {},
        synthetic_cold_users=targets,
        synthetic_cold_served_by=lambda user_id: user_id in any_history,
    )

    assert [result.synthetic_cold_slices[bucket].n_fallback_served for bucket in TEST_BUCKETS] == [
        2,
        0,
        0,
        0,
    ]
    assert harness.routing_is_correct(result) is False


def test_routing_cannot_be_correct_when_it_was_never_measured() -> None:
    result = evaluate({}, {}, {}, synthetic_cold_users=_targets())
    assert harness.routing_is_correct(result) is False
    assert harness.routing_is_correct(evaluate({}, {}, {})) is False


def test_the_expected_fallback_count_follows_adr_0001s_threshold() -> None:
    result = evaluate({}, {}, {}, synthetic_cold_users=_targets())
    for bucket, slice_ in result.synthetic_cold_slices.items():
        expected = harness.expected_fallback_served(slice_)
        assert expected == (2 if bucket < COLD_START_THRESHOLD else 0)


def test_the_metric_names_are_the_ones_adr_0011_names() -> None:
    result = evaluate(
        {},
        {},
        {},
        k=K_CANDIDATES,
        synthetic_cold_users=_targets(),
        synthetic_cold_served_by=lambda _user_id: False,
    )

    names = set(harness.metrics(result, suffix=harness.SUFFIX_AT_K_CANDIDATES))
    for bucket in (0, 1, 3, 10):
        assert f"synth_cold_recall_at_k_candidates_h{bucket}" in names
        assert f"synth_cold_ndcg_at_k_candidates_h{bucket}" in names
        assert f"synth_cold_fallback_served_h{bucket}" in names

    ranker_names = set(harness.metrics(result, suffix=harness.SUFFIX_AT_K))
    assert "synth_cold_recall_at_k_h10" in ranker_names
    assert harness.ROUTING_TAG == "synth_cold_routing_ok"


def test_an_unmeasured_bucket_logs_no_fallback_metric() -> None:
    result = evaluate({}, {}, {}, synthetic_cold_users=_targets())
    names = set(harness.metrics(result, suffix=harness.SUFFIX_AT_K))
    assert not any(name.startswith("synth_cold_fallback_served") for name in names)


# --- trainer glue ------------------------------------------------------------


def test_prepare_is_the_identity_without_a_cohort(tmp_path: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    from src.data.split import temporal_split

    split = temporal_split(_train_ratings().assign(timestamp=range(len(_train_ratings()))))
    frame, cohort = harness.prepare(
        split, logger=harness.logging.getLogger("test"), path=tmp_path / "absent.parquet"
    )
    assert cohort is None
    assert frame is split.train


def test_prepare_attaches_only_history_rows(tmp_path: Path) -> None:
    from src.data.split import temporal_split

    split = temporal_split(_timestamped_ratings())

    path = tmp_path / "users.parquet"
    frame = generate_cohort(
        split.train, split.cutoff, buckets=TEST_BUCKETS, users_per_bucket=TEST_USERS_PER_BUCKET
    )
    write_cohort(
        frame,
        path,
        provenance=build_provenance(
            frame,
            cutoff=split.cutoff,
            seed=SYNTH_COLD_SEED,
            buckets=TEST_BUCKETS,
            users_per_bucket=TEST_USERS_PER_BUCKET,
            data_version=read_movielens_data_version(),
        ),
    )

    attached, cohort = harness.prepare(split, logger=harness.logging.getLogger("test"), path=path)
    assert cohort is not None
    assert len(attached) == len(split.train) + TEST_USERS_PER_BUCKET * sum(TEST_BUCKETS)
    assert list(attached.columns) == list(split.train.columns)

    # No target movie of any cohort user made it into the training frame.
    synthetic_rows = attached[attached["userId"] >= SYNTH_COLD_USER_ID_BASE]
    pairs = set(zip(synthetic_rows["userId"], synthetic_rows["movieId"], strict=True))
    for users in cohort.targets_by_bucket.values():
        for user_id, targets in users.items():
            assert not {(user_id, target) for target in targets} & pairs

    ratingless = split.train.drop(columns="rating")
    attached_ratingless = harness.attach_history(ratingless, cohort)
    assert list(attached_ratingless.columns) == list(ratingless.columns)
    assert len(attached_ratingless) == len(attached)


def test_prepare_refuses_a_cohort_anchored_to_a_different_cutoff(tmp_path: Path) -> None:
    from src.data.split import temporal_split

    split = temporal_split(_timestamped_ratings())

    path = tmp_path / "users.parquet"
    frame = generate_cohort(
        split.train, CUTOFF, buckets=TEST_BUCKETS, users_per_bucket=TEST_USERS_PER_BUCKET
    )
    write_cohort(
        frame,
        path,
        provenance=build_provenance(
            frame,
            cutoff=CUTOFF + 1,
            seed=SYNTH_COLD_SEED,
            buckets=TEST_BUCKETS,
            users_per_bucket=TEST_USERS_PER_BUCKET,
            data_version=read_movielens_data_version(),
        ),
    )

    with pytest.raises(harness.CohortCutoffMismatchError, match="anchored to cutoff"):
        harness.prepare(split, logger=harness.logging.getLogger("test"), path=path)


# --- migration ---------------------------------------------------------------

MIGRATION = Path("alembic/versions/0015_synth_cold_tenant.py")


def test_the_tenant_migration_stays_on_the_one_linear_chain() -> None:
    """The newest migration names the head; earlier ones only hold the invariant.

    0015 stopped being the newest when 0016 landed, so this now holds the
    single-head invariant and ``tests/unit/test_tenant_champion_migration.py``
    names the head.
    """
    source = MIGRATION.read_text()
    assert 'revision: str = "0015_synth_cold_tenant"' in source
    assert 'down_revision: str | None = "0014_user_preferences"' in source

    revisions: set[str] = set()
    parents: set[str] = set()
    for path in Path("alembic/versions").glob("*.py"):
        text = path.read_text()
        revision = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert revision is not None
        revisions.add(revision.group(1))
        if down is not None and down.group(1):
            parents.add(down.group(1))

    assert len(revisions - parents) == 1, f"the migration graph has branched: {revisions - parents}"
    assert "0015_synth_cold_tenant" in parents


def test_the_tenant_row_is_additive_and_reversible() -> None:
    source = MIGRATION.read_text()
    upgrade, downgrade = source.split("def downgrade() -> None:", 1)

    assert "INSERT INTO public.tenants" in upgrade
    assert "'synth_cold'" in upgrade
    # Production is additive-migrations-only (ADR 0013): re-running this on a
    # database that already has the row must be a no-op, not a failure.
    assert "ON CONFLICT (id) DO NOTHING" in upgrade
    # The delete is plain: a foreign key from some future tenant-scoped row
    # should block the downgrade rather than quietly take that row with it.
    assert "DELETE FROM public.tenants WHERE id = 'synth_cold';" in downgrade
    assert "CASCADE" not in re.sub(r"#.*", "", downgrade)
    assert "DROP TABLE" not in downgrade
