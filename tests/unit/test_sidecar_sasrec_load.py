"""Loading and serving a SASRec bundle from the private model sidecar (W8).

Four things are pinned here, and only the first is about SASRec at all:

1. **The fused-attention NaN defect, at the configured depth.** In ``eval()``
   mode PyTorch's fastpath returns NaN for a fully-masked query position, so a
   left-padded history encodes to NaN and retrieval returns nothing. It is
   *depth-dependent*: at one encoder block the corruption stays in the padded
   rows, and at two — ADR 0016's configuration — it reaches the last position,
   which is the only vector retrieval reads. ``TestTheDefectItself`` measures
   both depths so the rest of the file is provably not vacuous, and every
   equivalence fixture runs at ``num_blocks=2`` for the same reason.
2. **Equivalence with the offline path** at histories of 1, 3, 12, 49 and 50.
   The first four are padded and would all be NaN unguarded; 50 fills the window
   exactly and is clean either way, which is why it is here — it pins the
   boundary from the side that cannot fail, so a fixture set that started passing
   for the wrong reason is still distinguishable.
3. **Fail-closed startup.** Every way a bundle can be unrealisable raises out of
   ``load``, which runs inside ``lifespan``, which kills the worker before it
   joins uvicorn's accept loop.
4. **That cold users did not move.** The schema 2 route table is new machinery on
   a path with published numbers against it; the incumbent booster has to score a
   cold user's request to the bit.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest
import torch

from src.feature_contract import FEATURE_COLUMNS
from src.models.artifacts import (
    INDEX_TYPE_FLAT_IP_EXACT,
    RANKER_ROUTE_FALLBACK,
    RANKER_ROUTE_LEARNED,
    RETRIEVER_FAMILY_ITEM_ITEM,
    RETRIEVER_FAMILY_SASREC,
    ArtifactRef,
    CandidateIndex,
    CandidateRetrieval,
    RankerRef,
    RetrieverRef,
    ServingArtifactBundle,
    ServingManifest,
    file_sha256,
)
from src.models.candidates.sasrec import SASRecConfig, SASRecEncoder, SASRecModel
from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME, MODEL_FILENAME, export_sasrec
from src.serving import sequence_retrieval
from src.serving.model_server import ModelRankingService
from src.serving.policy import CANDIDATE_SOURCE_POPULARITY_FILL
from src.serving.sequence_retrieval import (
    EncoderProducesNonFiniteVectorsError,
    SequenceBundleIncompleteError,
    top_up_to_limit,
)

TRAINED_AT = "2026-09-05T00:00:00+00:00"
FEATURE_VERSION = "feast-phase3-v1"

# ADR 0016's depth. The defect this file exists for does not reproduce at one
# block, so a fixture that quietly dropped to one would pass and prove nothing.
CONFIGURED_BLOCKS = 2
# The deployed window. 50 is load-bearing in two directions: every history below
# it is left-padded (the NaN path), and a history of exactly 50 is not.
WINDOW = 50
# Threshold-10 routing, as the target bundle declares it.
COLD_START_THRESHOLD = 10
# Wide enough that a 50-item history plus 20 candidates never exhausts the
# catalog — otherwise a short result would be about the fixture, not the code.
CATALOG = 150
FIRST_MOVIE_ID = 1000
RETRIEVAL_LIMIT = 20

# The lengths the brief names, and why each one is in the set.
HISTORY_LENGTHS = (1, 3, 12, 49, 50)


# --- the shared-encoder dependency -----------------------------------------


@pytest.fixture
def fastpath_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stand in for O-9/W17 until it lands on ``main``.

    The sidecar deliberately does not carry its own copy of the one-line fix —
    it imports it from the shared encoder path so training, evaluation and
    serving can never disagree about which numerics the published metrics were
    measured under. That leaves this suite with a dependency that does not exist
    yet, so the fixture supplies it in exactly the shape the loader expects: a
    callable ``disable_attention_fastpath`` on the shared module.

    It disables the fastpath *as well as* exporting the hook, and that is not
    belt-and-braces — it is what W17 landing actually means. The defect is in the
    shared encoder, so it corrupts the offline retrieval path too: with the
    fastpath live, ``SASRecModel.recommend_from_history`` returns ``[]`` for a
    padded history whether it is called from ``src/evaluation`` or from here.
    A fixture that guarded only the sidecar would compare the sidecar against a
    broken offline baseline and both would agree on nothing.

    **When W17 lands, delete this fixture**, not the tests that use it. The
    tests are the regression coverage; the fixture is scaffolding for a missing
    dependency, and ``TestTheFastpathGuardIsADependency`` is what will tell you
    the real thing is wired up.
    """
    previous = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(False)

    def disable_attention_fastpath() -> None:
        torch.backends.mha.set_fastpath_enabled(False)

    from src.models.candidates import sasrec as shared_encoder

    monkeypatch.setattr(
        shared_encoder,
        sequence_retrieval.FASTPATH_GUARD_SYMBOL,
        disable_attention_fastpath,
        raising=False,
    )
    try:
        yield
    finally:
        # Global process state. Restoring it is what stops one test from
        # deciding another test's answer.
        torch.backends.mha.set_fastpath_enabled(previous)


@pytest.fixture
def unguarded_fastpath() -> Iterator[None]:
    """Torch's default: the fastpath on, and the defect live."""
    previous = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(True)
    try:
        yield
    finally:
        torch.backends.mha.set_fastpath_enabled(previous)


# --- fixtures on disk -------------------------------------------------------


def _training_frame() -> pd.DataFrame:
    """A deterministic interaction log wide enough to retrieve 20 unseen items from."""
    rows: list[tuple[int, int, int]] = []
    for user in range(1, 21):
        for step in range(WINDOW + 12):
            movie = FIRST_MOVIE_ID + (user * 7 + step * 3) % CATALOG
            rows.append((user, movie, step))
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    return frame.drop_duplicates(subset=["userId", "movieId"], keep="first")


def _fitted_sasrec(*, num_blocks: int = CONFIGURED_BLOCKS, faiss_exact: bool = True) -> SASRecModel:
    return SASRecModel(
        config=SASRecConfig(
            max_sequence_length=WINDOW,
            hidden_dim=16,
            num_blocks=num_blocks,
            num_heads=2,
            feedforward_dim=32,
            dropout=0.2,
            negative_count=4,
            batch_size=64,
            epochs=1,
            faiss_exact=faiss_exact,
            seed=42,
        ),
        cold_start_threshold=COLD_START_THRESHOLD,
    ).fit(_training_frame())


def _unnamed_booster(path: Path, *, labels: list[float]) -> None:
    """Train the way ``LGBMRanker.fit`` does: from a bare matrix, so no names.

    The two routes are distinguished by their *labels* rather than by a seed.
    LightGBM's seed only bites when feature or row sampling is on, and this
    training is deterministic with neither — two boosters that differed only by
    seed would be byte-identical, and the route test that relies on them
    disagreeing would pass for the wrong reason.
    """
    columns = len(FEATURE_COLUMNS)
    rows = len(labels)
    frame = np.arange(columns * rows, dtype=np.float64).reshape(rows, columns) % 13.0
    booster = lgb.train(
        {
            "objective": "lambdarank",
            "num_leaves": 4,
            "min_data_in_leaf": 1,
            "verbose": -1,
            "num_threads": 1,
            "deterministic": True,
            "force_row_wise": True,
            "seed": 0,
        },
        lgb.Dataset(
            frame,
            label=np.array(labels, dtype=np.float64),
            group=[rows],
            free_raw_data=False,
        ),
        num_boost_round=5,
    )
    booster.save_model(str(path))


# Two clearly different ranking objectives, so a booster swap is observable.
LEARNED_LABELS = [4.0, 3.0, 2.0, 1.0, 0.0, 0.0]
INCUMBENT_LABELS = [0.0, 0.0, 1.0, 2.0, 3.0, 4.0]


def _ranker_ref(path: Path, *, version: str, labels: list[float]) -> ArtifactRef:
    _unnamed_booster(path, labels=labels)
    return ArtifactRef(
        artifact_type="lightgbm-lambdarank",
        version=version,
        filename=path.name,
        sha256=file_sha256(path),
    )


def _blob(path: Path, *, artifact_type: str, version: str, payload: object) -> ArtifactRef:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return ArtifactRef(
        artifact_type=artifact_type,
        version=version,
        filename=path.name,
        sha256=file_sha256(path),
    )


def _publish_sasrec_bundle(
    directory: Path,
    *,
    model: SASRecModel | None = None,
    params: dict[str, Any] | None = None,
    write_artifact_manifest: bool = True,
) -> Path:
    """Write a schema 2 SASRec bundle and return its serving manifest path.

    The archive and its ``sasrec-manifest.json`` are produced by the real
    exporter, so the bundle this loads is the shape ``src/training`` publishes
    rather than a hand-rolled approximation of it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    fitted = model if model is not None else _fitted_sasrec()
    export_sasrec(fitted, directory)
    if not write_artifact_manifest:
        (directory / MANIFEST_FILENAME).unlink()

    archive = directory / MODEL_FILENAME
    encoder = ArtifactRef(
        artifact_type="sasrec-encoder",
        version="sasrec-v1",
        filename=archive.name,
        sha256=file_sha256(archive),
    )
    # The vocabulary and config roles are checksum-pinned declarations: the
    # loader rebuilds from the archive (which already carries both) and holds the
    # result to the serving manifest's declared params. They are written here so
    # the manifest's own artifact requirements are satisfiable.
    vocabulary = _blob(
        directory / "sasrec-vocabulary.json",
        artifact_type="sasrec-vocabulary",
        version="sasrec-v1",
        payload=sorted(fitted._index_to_item.values()),
    )
    config = _blob(
        directory / "sasrec-config.json",
        artifact_type="sasrec-config",
        version="sasrec-v1",
        payload=fitted.config.as_params(),
    )
    declared: dict[str, Any] = {
        "max_sequence_length": WINDOW,
        "cold_start_threshold": COLD_START_THRESHOLD,
        "exclusion_policy": "watched-and-dismissed-excluded-v1",
        "index_type": INDEX_TYPE_FLAT_IP_EXACT,
    }
    declared.update(params or {})

    learned = RankerRef(
        artifact=_ranker_ref(directory / "ranker.txt", version="learned-v1", labels=LEARNED_LABELS)
    )
    fallback = RankerRef(
        artifact=_ranker_ref(
            directory / "incumbent.txt", version="incumbent-v1", labels=INCUMBENT_LABELS
        )
    )
    manifest = ServingManifest(
        tenant_id="demo",
        retriever=RetrieverRef(
            family=RETRIEVER_FAMILY_SASREC,
            artifacts={"encoder": encoder, "vocabulary": vocabulary, "config": config},
            params=declared,
        ),
        rankers={RANKER_ROUTE_LEARNED: learned, RANKER_ROUTE_FALLBACK: fallback},
        feature_version=FEATURE_VERSION,
        trained_at=TRAINED_AT,
    )
    path = directory / "manifest.json"
    manifest.write(path)
    return path


def _model_history(length: int) -> list[int]:
    """``length`` in-vocabulary movies in the order the *encoder* was trained on.

    Oldest to newest, so the last element is the most recently watched title —
    which is what ``_sequence_tensor`` puts at the final position, and the final
    position is the only vector retrieval reads.
    """
    return [FIRST_MOVIE_ID + (index * 3) % CATALOG for index in range(length)]


def _request_history(length: int) -> list[int]:
    """The same history in the order a *rank request* actually carries it.

    Most recently watched first. This is not a detail of the fixture: the
    coordinator sorts descending by event rank because item-item's
    "because you watched…" attribution wants the newest seed first
    (``src/serving/recommendations.py``), and SASRec's contract is the exact
    opposite. Every equivalence test below feeds the sidecar this order and
    compares against the offline model fed ``_model_history``, so the conversion
    in ``_encoder_window`` is what is being pinned — not just the retrieval.
    """
    return list(reversed(_model_history(length)))


# --- 1. the defect itself ---------------------------------------------------


class TestTheDefectItself:
    """Measure the bug, so the rest of the file is provably not vacuous.

    Without this, a suite that passes proves only that something passes. These
    two tests establish that the equivalence fixtures below are exercising a
    path that is genuinely broken by default, and that a single-layer version of
    the same coverage would have been worthless.
    """

    @pytest.mark.parametrize("history_length", [1, 3, 12, 49])
    def test_two_blocks_return_nan_for_a_padded_history_when_the_fastpath_is_on(
        self, unguarded_fastpath: None, history_length: int
    ) -> None:
        encoded = _encode_at_depth(CONFIGURED_BLOCKS, history_length)

        assert not bool(torch.isfinite(encoded).all()), (
            "the fused-attention fastpath no longer corrupts a padded history at "
            f"{CONFIGURED_BLOCKS} blocks. If torch fixed it upstream, this file's guard is now "
            "belt-and-braces rather than load-bearing — confirm before relaxing anything."
        )

    def test_the_corruption_is_depth_dependent_and_absent_at_one_block(
        self, unguarded_fastpath: None
    ) -> None:
        """One block is clean; two is not. This is why depth is pinned in the fixtures.

        At a single layer the NaN stays confined to the padded rows and never
        reaches the last position. At two, the padded row feeds the second layer,
        the causal mask lets the last position attend over it, and the corruption
        lands in exactly the vector retrieval reads.
        """
        assert bool(torch.isfinite(_encode_at_depth(1, 12)).all())
        assert not bool(torch.isfinite(_encode_at_depth(2, 12)).all())

    def test_a_full_window_is_clean_at_every_depth(self, unguarded_fastpath: None) -> None:
        """The control: 50 items fill the window, so no query position is fully masked."""
        for blocks in (1, CONFIGURED_BLOCKS):
            assert bool(torch.isfinite(_encode_at_depth(blocks, WINDOW)).all())


@contextlib.contextmanager
def _without_the_shared_fix() -> Iterator[None]:
    """Run the real encoder as if W17 had never landed.

    W17 put `torch.backends.mha.set_fastpath_enabled(False)` inside
    `SASRecEncoder.encode_positions`, so it applies on *every* encode. That is
    the right place for it, and it has a side effect worth naming: the defect can
    no longer be observed through this project's encoder at all, which would
    leave the fixtures below unable to show they are exercising a genuinely
    broken path.

    Neutralising that one call — rather than rebuilding a parallel transformer
    stack — keeps these tests measuring the real class, and makes them a
    regression test for W17 itself: if someone deletes that line, the tests that
    assert corruption *stop* failing and the ones asserting a clean encode start
    to. Either way the suite notices.
    """
    original = torch.backends.mha.set_fastpath_enabled
    torch.backends.mha.set_fastpath_enabled = lambda _enabled: None  # type: ignore[assignment]
    original(True)
    try:
        yield
    finally:
        torch.backends.mha.set_fastpath_enabled = original  # type: ignore[assignment]
        original(False)


def _encode_at_depth(num_blocks: int, history_length: int) -> torch.Tensor:
    """Encode a left-padded history through an untrained encoder of a given depth."""
    torch.manual_seed(0)
    config = SASRecConfig(
        max_sequence_length=WINDOW, hidden_dim=16, num_blocks=num_blocks, num_heads=2
    )
    encoder = SASRecEncoder(CATALOG + 2, config)
    encoder.eval()
    sequence = torch.zeros((1, WINDOW), dtype=torch.long)
    sequence[0, -history_length:] = torch.arange(1, history_length + 1)
    with _without_the_shared_fix(), torch.no_grad():
        return encoder(sequence)


# --- 2. the fastpath guard is a dependency, not a copy ----------------------


class TestTheFastpathGuardIsADependency:
    def test_a_missing_symbol_is_not_a_refusal_because_the_fix_can_be_call_time(
        self, tmp_path: Path, unguarded_fastpath: None
    ) -> None:
        """W17 applies the toggle inside `encode_positions`, on every encode.

        This test previously asserted the opposite — that no exported symbol and
        no import side effect meant refusal. That premise was wrong: a symbol
        lookup cannot see a fix that only exists once the code runs, so refusing
        on its absence would reject a correctly-fixed bundle.

        What replaces it is not weaker. The guard now *reports* how the fix
        arrived, and `_assert_encoder_is_finite` decides whether the sidecar
        starts by calling the real encoder at the lengths that were broken. The
        safety property — a bundle whose encoder is genuinely unfixed must not
        serve — is asserted directly below and by the no-op-hook test, both of
        which exercise behaviour rather than the presence of a name.
        """
        assert (
            sequence_retrieval._resolve_fastpath_guard()
            == sequence_retrieval.GUARD_SOURCE_CALL_TIME
        )

    def test_an_encoder_that_is_still_broken_stops_the_deployment(
        self, tmp_path: Path, unguarded_fastpath: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property the removed test was really protecting.

        The sidecar could disable the fastpath itself in one line. It deliberately
        does not: a second copy of a global toggle is how serving and training end
        up disagreeing about which numerics a published metric was measured under.
        So it depends on the shared fix — and proves the dependency held by
        encoding a short history and looking at the vector, rather than by
        trusting that a name exists.
        """
        path = _publish_sasrec_bundle(tmp_path / "bundle")
        # Re-enable the defect after loading would otherwise have disabled it, so
        # the encoder really is unfixed at probe time.
        monkeypatch.setattr(
            sequence_retrieval,
            "_resolve_fastpath_guard",
            lambda: sequence_retrieval.GUARD_SOURCE_CALL_TIME,
        )
        with _without_the_shared_fix():
            with pytest.raises(EncoderProducesNonFiniteVectorsError) as error:
                ServingArtifactBundle.load(path)

        # Actionable at 3am by someone who has never heard of this defect.
        assert "fastpath" in str(error.value).lower()

    def test_the_guard_is_taken_from_the_shared_module_when_it_exports_a_hook(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        bundle = ServingArtifactBundle.load(_publish_sasrec_bundle(tmp_path / "bundle"))

        assert bundle.retriever is not None
        assert bundle.retriever.fastpath_guard_source == sequence_retrieval.GUARD_SOURCE_HOOK

    def test_the_guard_is_accepted_when_the_shared_module_disables_it_on_import(
        self, tmp_path: Path
    ) -> None:
        """W17 may land as an import side effect rather than a callable.

        Which shape it takes is not this lane's decision, so both are accepted
        and the boot log records which one was found. Only "neither" is a
        failure.
        """
        previous = torch.backends.mha.get_fastpath_enabled()
        torch.backends.mha.set_fastpath_enabled(False)
        try:
            bundle = ServingArtifactBundle.load(_publish_sasrec_bundle(tmp_path / "bundle"))
        finally:
            torch.backends.mha.set_fastpath_enabled(previous)

        assert bundle.retriever is not None
        assert bundle.retriever.fastpath_guard_source == sequence_retrieval.GUARD_SOURCE_IMPORT

    def test_a_guard_that_resolves_but_does_not_work_is_still_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unguarded_fastpath: None
    ) -> None:
        """The behavioural probe, not the symbol lookup, is what actually protects us.

        A hook that exists and does nothing is exactly what a torch upgrade that
        moves the flag would produce, and it is indistinguishable from a working
        one by inspection.
        """
        from src.models.candidates import sasrec as shared_encoder

        monkeypatch.setattr(
            shared_encoder,
            sequence_retrieval.FASTPATH_GUARD_SYMBOL,
            lambda: None,
            raising=False,
        )
        path = _publish_sasrec_bundle(tmp_path / "bundle")

        with _without_the_shared_fix():
            with pytest.raises(
                EncoderProducesNonFiniteVectorsError, match="non-finite query vector"
            ):
                ServingArtifactBundle.load(path)


# --- 3. equivalence with the offline path -----------------------------------


class TestOfflineEquivalence:
    """The sidecar must retrieve exactly what the offline model retrieves.

    Ids *and* order, against the fitted model rather than against the reloaded
    one — comparing a loaded model to itself would pass with the export, the
    serving manifest and the adapter all removed.
    """

    @pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
    def test_the_sidecar_matches_the_offline_retrieval_exactly(
        self, tmp_path: Path, fastpath_guard: None, history_length: int
    ) -> None:
        fitted = _fitted_sasrec()
        expected = fitted.recommend_from_history(_model_history(history_length), RETRIEVAL_LIMIT)

        path = _publish_sasrec_bundle(tmp_path / "bundle", model=fitted)
        bundle = ServingArtifactBundle.load(path)
        assert bundle.retriever is not None
        retrieval = bundle.retriever.retrieve(
            _request_history(history_length), limit=RETRIEVAL_LIMIT
        )

        assert retrieval.movie_ids == expected
        # A full list, not "as many as it happened to find". A padded history
        # that silently returned three items would satisfy an equality check
        # against an equally broken offline call.
        assert len(retrieval.movie_ids) == RETRIEVAL_LIMIT

    @pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
    def test_no_history_length_is_answered_by_an_empty_retrieval(
        self, tmp_path: Path, fastpath_guard: None, history_length: int
    ) -> None:
        """The failure mode restated as a serving promise.

        An empty retrieval is what the coordinator turns into a popularity
        response, so "encodes to NaN" and "quietly gets popularity" are the same
        event seen from two places. Sub-window users must never take that path.
        """
        bundle = ServingArtifactBundle.load(_publish_sasrec_bundle(tmp_path / "bundle"))
        assert bundle.retriever is not None

        retrieval = bundle.retriever.retrieve(
            _request_history(history_length), limit=RETRIEVAL_LIMIT
        )

        assert retrieval.contributions
        assert retrieval.seed_count > 0

    def test_a_history_longer_than_the_window_keeps_the_newest_titles(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        """The truncation half of the wire-order conversion, and the costlier half.

        A user with more than ``max_sequence_length`` positives is the common
        case, not an edge one. The request carries them newest first, so slicing
        the *front* of the wire order — which is what handing it straight to the
        model does — keeps the newest ids but presents them reversed; slicing the
        model's order without converting first would keep the user's *oldest* 50
        titles and throw away everything recent. Both are silent.

        The second assertion is what makes this a real test rather than a
        restatement: the oldest window and the newest window must retrieve
        different things, otherwise the first assertion would hold either way.
        """
        fitted = _fitted_sasrec()
        long_history = _model_history(WINDOW + 10)
        newest_window = long_history[-WINDOW:]
        oldest_window = long_history[:WINDOW]
        expected = fitted.recommend_from_history(newest_window, RETRIEVAL_LIMIT)

        bundle = ServingArtifactBundle.load(
            _publish_sasrec_bundle(tmp_path / "bundle", model=fitted)
        )
        assert bundle.retriever is not None
        retrieval = bundle.retriever.retrieve(list(reversed(long_history)), limit=RETRIEVAL_LIMIT)

        assert retrieval.movie_ids == expected
        assert expected != fitted.recommend_from_history(oldest_window, RETRIEVAL_LIMIT)
        # Only the window drove the query, so a longer history is not counted whole.
        assert retrieval.seed_count == WINDOW

    def test_the_unguarded_encoder_would_have_failed_these_fixtures(
        self, tmp_path: Path, unguarded_fastpath: None
    ) -> None:
        """Proof the fixtures above are load-bearing rather than incidentally green.

        Built without going through ``load``, because ``load`` refuses an
        unguarded encoder — which is the point, but it also means the only way to
        observe what a booted-anyway sidecar would have served is to bypass it.
        """
        fitted = _fitted_sasrec()

        with _without_the_shared_fix():
            assert fitted.recommend_from_history(_model_history(12), RETRIEVAL_LIMIT) == []
        # And the boundary from the other side: a full window still works, so a
        # deployment would have looked healthy for its longest-history users.
        assert len(fitted.recommend_from_history(_model_history(WINDOW), RETRIEVAL_LIMIT)) == (
            RETRIEVAL_LIMIT
        )

    def test_a_dismissed_seed_never_steers_the_query_vector(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        """ADR 0012: a dismissal drops the seed, an exclusion only hides the result.

        Checked against the offline model asked the narrower question, so the
        adapter cannot pass by suppressing the title after the encoder has
        already been steered by it.
        """
        fitted = _fitted_sasrec()
        # The most recently watched title, which is the *last* element in the
        # model's order and the *first* on the wire.
        dismissed = {_model_history(12)[-1]}
        expected = fitted.recommend_from_history(
            [movie for movie in _model_history(12) if movie not in dismissed],
            RETRIEVAL_LIMIT,
            excluded_movie_ids=dismissed,
        )

        bundle = ServingArtifactBundle.load(
            _publish_sasrec_bundle(tmp_path / "bundle", model=fitted)
        )
        assert bundle.retriever is not None
        retrieval = bundle.retriever.retrieve(
            _request_history(12), limit=RETRIEVAL_LIMIT, dismissed_movie_ids=dismissed
        )

        assert retrieval.movie_ids == expected


# --- 4. fail-closed startup -------------------------------------------------


class TestFailClosedStartup:
    def test_a_bundle_without_its_artifact_manifest_is_refused(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        path = _publish_sasrec_bundle(tmp_path / "bundle", write_artifact_manifest=False)

        with pytest.raises(SequenceBundleIncompleteError, match=MANIFEST_FILENAME):
            ServingArtifactBundle.load(path)

    def test_a_declared_window_the_encoder_disagrees_with_is_refused(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        """``validate`` proves the parameter is present; only loading proves it is true."""
        path = _publish_sasrec_bundle(
            tmp_path / "bundle", params={"max_sequence_length": WINDOW + 1}
        )

        with pytest.raises(SequenceBundleIncompleteError, match="max_sequence_length"):
            ServingArtifactBundle.load(path)

    def test_a_declared_threshold_the_encoder_disagrees_with_is_refused(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        path = _publish_sasrec_bundle(tmp_path / "bundle", params={"cold_start_threshold": 25})

        with pytest.raises(SequenceBundleIncompleteError, match="cold_start_threshold"):
            ServingArtifactBundle.load(path)

    def test_an_inexact_index_is_refused_under_a_manifest_claiming_exact_search(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        """``faiss_exact`` defaults to False, and an IVF rebuild is not deterministic.

        The manifest can only say ``flat-ip-exact``; whether the encoder config
        agrees is a fact about the archive, and serving the disagreement would
        mean serving a retriever nobody measured.
        """
        path = _publish_sasrec_bundle(tmp_path / "bundle", model=_fitted_sasrec(faiss_exact=False))

        with pytest.raises(SequenceBundleIncompleteError, match="faiss_exact"):
            ServingArtifactBundle.load(path)

    def test_a_tampered_encoder_archive_is_refused(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        path = _publish_sasrec_bundle(tmp_path / "bundle")
        (tmp_path / "bundle" / MODEL_FILENAME).write_bytes(b"tampered")

        with pytest.raises(ValueError, match="checksum mismatch"):
            ServingArtifactBundle.load(path)


# --- 5. per-route ranker selection and cold-user identity -------------------


class _OnlineResponse:
    def __init__(self, values: dict[str, list[Any]]) -> None:
        self._values = values

    def to_dict(self, include_event_timestamps: bool = False) -> dict[str, list[Any]]:
        return self._values


class _FeatureStore:
    """A pure function of the entity rows, so two bundles cannot differ by luck."""

    def get_online_features(
        self, *, features: list[str], entity_rows: list[dict[str, object]]
    ) -> _OnlineResponse:
        values: dict[str, list[Any]] = {}
        for offset, column in enumerate(FEATURE_COLUMNS):
            values[column] = [
                float((int(str(row["item_id"])) * (offset + 1)) % 97) for row in entity_rows
            ]
            values[f"{column}__ts"] = [1_756_000_000.0] * len(entity_rows)
        return _OnlineResponse(values)


# Wide enough that a warm history of ten titles still leaves plenty of unseen
# neighbours to rank. A near-exhausted index would hand the ranker a single
# candidate, and one candidate cannot show that two boosters disagree.
_HISTORIES = {user: {(user * 3 + step) % 60 + 1 for step in range(18)} for user in range(1, 26)}


def _publish_itemitem_bundle(
    directory: Path,
    *,
    schema_v2: bool,
    declare_threshold: bool = True,
) -> Path:
    """A v1 demo bundle, or the v2 bundle that must not move a cold user off it.

    Both write the same candidate index bytes and the same ``ranker.txt`` bytes,
    so any difference in a cold user's answer is the route table's doing and
    nothing else's. The v2 bundle's *learned* route is a different booster on
    purpose: without that, picking the wrong route would be undetectable.
    """
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / "candidate-index.json"
    CandidateIndex.build(_HISTORIES).write(index_path)
    index = ArtifactRef(
        artifact_type=RETRIEVER_FAMILY_ITEM_ITEM,
        version="demo-itemitem-v1",
        filename=index_path.name,
        sha256=file_sha256(index_path),
    )
    incumbent = _ranker_ref(
        directory / "ranker.txt", version="incumbent-v1", labels=INCUMBENT_LABELS
    )
    if not schema_v2:
        ServingManifest(
            tenant_id="demo",
            candidate=index,
            ranker=incumbent,
            feature_version=FEATURE_VERSION,
            trained_at=TRAINED_AT,
        ).write(directory / "manifest.json")
        return directory / "manifest.json"

    learned = _ranker_ref(directory / "learned.txt", version="learned-v1", labels=LEARNED_LABELS)
    params: dict[str, Any] = {}
    if declare_threshold:
        params["cold_start_threshold"] = COLD_START_THRESHOLD
    ServingManifest(
        tenant_id="demo",
        retriever=RetrieverRef(
            family=RETRIEVER_FAMILY_ITEM_ITEM, artifacts={"index": index}, params=params
        ),
        rankers={
            RANKER_ROUTE_LEARNED: RankerRef(artifact=learned),
            RANKER_ROUTE_FALLBACK: RankerRef(artifact=incumbent),
        },
        feature_version=FEATURE_VERSION,
        trained_at=TRAINED_AT,
    ).write(directory / "manifest.json")
    return directory / "manifest.json"


def _rank(path: Path, history: list[int]) -> Any:
    service = ModelRankingService(ServingArtifactBundle.load(path), _FeatureStore())
    return service.rank(
        tenant_id="demo",
        user_id=100,
        positive_history_movie_ids=history,
        excluded_movie_ids=[],
        dismissed_movie_ids=[],
        limit=5,
        candidate_limit=10,
    )


class TestColdUsersDoNotChange:
    def test_a_cold_user_is_bit_identical_between_the_v1_and_v2_bundles(
        self, tmp_path: Path
    ) -> None:
        """The whole schema 2 route table must be invisible to a cold user.

        Exact float equality, not ``approx``: these are the same booster reading
        the same features over the same candidates, so anything but bit equality
        means something moved that should not have.
        """
        cold = [1, 2, 3]
        assert len(cold) < COLD_START_THRESHOLD

        v1 = _rank(_publish_itemitem_bundle(tmp_path / "v1", schema_v2=False), cold)
        v2 = _rank(_publish_itemitem_bundle(tmp_path / "v2", schema_v2=True), cold)

        assert [item.movie_id for item in v2.items] == [item.movie_id for item in v1.items]
        assert [item.score for item in v2.items] == [item.score for item in v1.items]
        assert v2.ranker_route == RANKER_ROUTE_FALLBACK
        assert v2.ranker_version == v1.ranker_version == "incumbent-v1"

    def test_a_warm_user_takes_the_learned_route_on_the_v2_bundle(self, tmp_path: Path) -> None:
        """The converse. Without it, "cold users did not change" could just mean
        the route table never selects anything."""
        warm = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        assert len(warm) >= COLD_START_THRESHOLD

        v1 = _rank(_publish_itemitem_bundle(tmp_path / "v1", schema_v2=False), warm)
        v2 = _rank(_publish_itemitem_bundle(tmp_path / "v2", schema_v2=True), warm)

        assert v2.ranker_route == RANKER_ROUTE_LEARNED
        assert v2.ranker_version == "learned-v1"
        # Same candidates — retrieval is unchanged — scored by a different booster.
        assert [item.movie_id for item in v2.items] == [item.movie_id for item in v1.items]
        assert [item.score for item in v2.items] != [item.score for item in v1.items]

    def test_a_v2_bundle_that_declares_no_threshold_keeps_the_single_learned_route(
        self, tmp_path: Path
    ) -> None:
        """Every bundle published before schema 2 declares no threshold, and must
        keep answering everything from the one route it always had."""
        result = _rank(
            _publish_itemitem_bundle(tmp_path / "v2", schema_v2=True, declare_threshold=False),
            [1, 2, 3],
        )

        assert result.ranker_route == RANKER_ROUTE_LEARNED


# --- 6. topping up to limit is the sidecar's job ----------------------------


class TestTopUpToLimit:
    def test_a_full_retrieval_is_handed_back_untouched(self) -> None:
        """The identity that keeps the incumbent path bit-identical.

        ``CandidateIndex.retrieve`` already fills from popularity before it
        returns, so the sidecar's top-up must be a provable no-op for item-item —
        not "equal", the same object.
        """
        index = CandidateIndex.build(_HISTORIES)
        retrieval = index.retrieve([1], limit=3)
        assert len(retrieval.contributions) == 3

        topped = top_up_to_limit(retrieval, limit=3, fill_order=index.popularity, blocked=set())

        assert topped.retrieval is retrieval
        assert topped.filled == 0

    def test_a_short_retrieval_is_padded_from_the_fill_order(self) -> None:
        short = CandidateRetrieval(contributions=(), seed_count=0, excluded_count=0)

        topped = top_up_to_limit(short, limit=3, fill_order=(10, 11, 12, 13), blocked={11})

        assert topped.retrieval.movie_ids == [10, 12, 13]
        assert topped.filled == 3
        assert all(
            contribution.source == CANDIDATE_SOURCE_POPULARITY_FILL
            for contribution in topped.retrieval.contributions
        )

    def test_an_exhausted_fill_order_reports_the_shortfall_rather_than_hiding_it(self) -> None:
        short = CandidateRetrieval(contributions=(), seed_count=0, excluded_count=0)

        topped = top_up_to_limit(short, limit=5, fill_order=(10, 11), blocked=set())

        assert topped.retrieval.movie_ids == [10, 11]
        assert topped.shortfall == 3

    def test_a_sasrec_bundle_publishes_no_fill_order(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        """Stated as a test because it is a decision, not an omission.

        The archive carries no popularity model — ``export_sasrec`` deliberately
        omits it, and the offline ranker run injects item-item's by hand — so the
        only fill order available to the sidecar would be the item vocabulary in
        sorted movie-id order. Labelling that ``popularity-fill`` in a prediction
        audit would be false. Shipping a popularity artifact beside the encoder
        is the fix, and it belongs to the training lane.
        """
        bundle = ServingArtifactBundle.load(_publish_sasrec_bundle(tmp_path / "bundle"))
        assert bundle.retriever is not None

        assert bundle.retriever.fill_order() == ()


# --- 7. the scored-retrieval tripwire ---------------------------------------


class TestRetrievalScoresReachTheAudit:
    # This was a strict xfail while `recommend_from_history_scored` did not exist.
    # W17 (#162) landed it, the tripwire flipped to a failure exactly as intended,
    # and it is now an ordinary assertion — the unscored branch in
    # `_scored_retrieval` remains only for a bundle whose model predates the method.
    def test_sasrec_candidates_carry_a_real_retrieval_score(
        self, tmp_path: Path, fastpath_guard: None
    ) -> None:
        """Every candidate scoring 0.0 makes an audit in which they all look identical.

        The contribution field is what a prediction audit replays to explain why
        one candidate outranked another before the ranker saw it. A retriever
        that reports the same number for all 500 is not explaining anything.
        """
        bundle = ServingArtifactBundle.load(_publish_sasrec_bundle(tmp_path / "bundle"))
        assert bundle.retriever is not None

        retrieval = bundle.retriever.retrieve(_request_history(12), limit=RETRIEVAL_LIMIT)

        assert retrieval.contributions
        assert any(
            contribution.contribution != 0.0 for contribution in retrieval.contributions
        ), "every SASRec candidate scored 0.0, so the retrieval score is a placeholder"

    def test_the_sidecar_consumes_a_scored_method_as_soon_as_one_exists(
        self, tmp_path: Path, fastpath_guard: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half of the tripwire: the consumer is already written.

        Pins the exact signature the sidecar expects, so the lane adding the
        method has a contract to build against rather than a guess.
        """
        bundle = ServingArtifactBundle.load(_publish_sasrec_bundle(tmp_path / "bundle"))
        assert bundle.retriever is not None

        def recommend_from_history_scored(
            movie_ids: list[int],
            k: int,
            *,
            excluded_movie_ids: set[int] | None = None,
        ) -> list[tuple[int, float]]:
            return [(FIRST_MOVIE_ID + index, 1.0 - index / 100) for index in range(k)]

        monkeypatch.setattr(
            bundle.retriever.model,
            sequence_retrieval.SCORED_RETRIEVAL_METHOD,
            recommend_from_history_scored,
            raising=False,
        )
        retrieval = bundle.retriever.retrieve(_request_history(12), limit=3)

        assert retrieval.movie_ids == [FIRST_MOVIE_ID, FIRST_MOVIE_ID + 1, FIRST_MOVIE_ID + 2]
        assert [contribution.contribution for contribution in retrieval.contributions] == [
            1.0,
            0.99,
            0.98,
        ]
