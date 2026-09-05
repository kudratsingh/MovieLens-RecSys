"""Realising a bundle's retrieval stage inside the private model sidecar.

``ServingArtifactBundle`` knows what a bundle *claims*; this module is what
turns that claim into something that can answer a rank request. It exists as
its own file for three reasons, and each one is a constraint rather than a
preference.

**The heavy imports have to be lazy.** Torch and FAISS are now installed in the
sidecar image (and only there — the slim API image still refuses all three of
implicit, torch and faiss, which ``tests/unit/test_serving_image_imports.py``
enforces). "Installed" is not "imported": a sidecar serving an item-item bundle
should not pay a torch import to start, and ``src.serving.model_server`` must
stay importable in an environment that has neither library. So every torch- and
FAISS-touching import in this module happens inside the function that needs it,
and the module's own import graph is stdlib plus ``src.models.artifacts``.

**The families disagree about more than retrieval.** The sidecar needs three
things from a retrieval stage — retrieve, a fill order to top a short result up
with, and a deterministic set of warm-up seeds — and only the first of those is
something a retriever naturally has. Keeping the other two on an adapter here,
rather than pushing them into ``CandidateIndex`` and ``SASRecModel``, is what
lets the sidecar treat both families identically without either model class
growing a serving concern.

**The attention fastpath defect is a load-time gate.** See
``_resolve_fastpath_guard`` below: an unguarded SASRec encoder returns NaN for
any left-padded history and therefore retrieves nothing, and a sidecar that
booted anyway would answer a warm user with the popularity fallback and call it
a healthy deployment.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.models.artifacts import (
    INDEX_TYPE_FLAT_IP_EXACT,
    RETRIEVER_FAMILY_SASREC,
    CandidateContribution,
    CandidateIndex,
    CandidateRetrieval,
    RetrieverRef,
)
from src.serving.policy import CANDIDATE_SOURCE_POPULARITY_FILL

logger = logging.getLogger(__name__)

# The module the shared SASRec encoder lives in, and the symbol O-9/W17 is
# expected to export from it once that work lands. Named as constants because
# the failure message has to be able to quote them: an operator reading
# "the encoder guard is missing" needs to be told exactly what would satisfy it.
SHARED_ENCODER_MODULE = "src.models.candidates.sasrec"
FASTPATH_GUARD_SYMBOL = "disable_attention_fastpath"

# Where the guard came from, for the boot log and for tests. Three values rather
# than a boolean because "W17 landed as a callable" and "W17 landed as an import
# side effect" are different facts about the tree, and the day the hook is
# renamed we want the log to say which one stopped being true.
GUARD_SOURCE_HOOK = "shared-encoder-hook"
GUARD_SOURCE_IMPORT = "shared-encoder-import"


class AttentionFastpathGuardUnavailableError(RuntimeError):
    """The shared encoder path does not (yet) disable the fused attention fastpath.

    Raised from ``load``, which runs inside the sidecar's ``lifespan``, so the
    worker dies before it joins uvicorn's accept loop. That is the whole point:
    the alternative to this exception is a sidecar that starts, reports healthy,
    encodes every sub-window history to NaN, retrieves zero candidates, and lets
    the coordinator degrade each of those requests to popularity — a silent
    quality outage that no gate in this system would catch.
    """


class EncoderProducesNonFiniteVectorsError(RuntimeError):
    """The load-time probe found a history length the encoder cannot represent.

    Distinct from the guard error above because it answers a different question.
    That one says "the fix is not in the tree"; this one says "the fix is in the
    tree and did not work" — a different bug, a different owner, and the only
    one of the two that a torch upgrade can reintroduce on its own.
    """


class SequenceBundleIncompleteError(ValueError):
    """The published bundle is not the shape this loader can rebuild an encoder from."""


class SidecarRetriever(Protocol):
    """What the sidecar needs from a retrieval stage, whatever family built it."""

    @property
    def family(self) -> str:
        """The manifest's name for this retrieval family."""

    def retrieve(
        self,
        positive_history_movie_ids: list[int],
        *,
        limit: int,
        excluded_movie_ids: Iterable[int] = (),
        dismissed_movie_ids: Iterable[int] = (),
    ) -> CandidateRetrieval:
        """At most ``limit`` candidates, with the provenance an audit needs."""

    def fill_order(self) -> Sequence[int]:
        """Items to top a short retrieval up with, best first, or empty for none."""

    def warmup_seed_movie_ids(self, count: int) -> list[int]:
        """A deterministic warm-up history: a pure function of the loaded bundle."""


@dataclass(frozen=True)
class ItemItemSidecarRetriever:
    """The shipped item-item index under the sidecar's contract.

    Deliberately thin. ``CandidateIndex.retrieve`` is forwarded untouched — not
    wrapped, not re-ordered, not re-filtered — because every incumbent number
    this project has published was measured through it, and the schema 2 work
    must leave the incumbent path bit-identical (``tests/unit/
    test_sidecar_sasrec_load.py::TestColdUsersDoNotChange``).
    """

    index: CandidateIndex

    @property
    def family(self) -> str:
        # Read off the loaded manifest by the caller in every real path; stated
        # here so the adapter can name itself in a log line without one.
        from src.models.artifacts import RETRIEVER_FAMILY_ITEM_ITEM

        return RETRIEVER_FAMILY_ITEM_ITEM

    def retrieve(
        self,
        positive_history_movie_ids: list[int],
        *,
        limit: int,
        excluded_movie_ids: Iterable[int] = (),
        dismissed_movie_ids: Iterable[int] = (),
    ) -> CandidateRetrieval:
        return self.index.retrieve(
            positive_history_movie_ids,
            limit=limit,
            excluded_movie_ids=excluded_movie_ids,
            dismissed_movie_ids=dismissed_movie_ids,
        )

    def fill_order(self) -> Sequence[int]:
        # The same order ``CandidateIndex.retrieve`` already fills from, which is
        # what makes the sidecar's top-up provably a no-op for this family.
        return self.index.popularity

    def warmup_seed_movie_ids(self, count: int) -> list[int]:
        """The lowest item ids this index can retrieve from, in sorted order.

        Moved here verbatim from ``ModelRankingService`` when the sidecar stopped
        assuming item-item. Sorted ids rather than the most popular ones: the
        warm input has to be a pure function of the bundle so every worker in a
        deployment — and every deployment of the same image — warms on identical
        data. Neighbour keys come first because a seed the index has no
        neighbours for exercises only the popularity fill, and the fill is not
        the path a warm user takes.
        """
        if self.index.neighbors:
            return sorted(self.index.neighbors)[:count]
        return sorted(self.index.popularity)[:count]


@dataclass(frozen=True)
class SASRecSidecarRetriever:
    """A loaded SASRec encoder plus its exact FAISS index, under the same contract.

    ``model`` is a ``SASRecModel`` and is typed loosely on purpose: naming the
    class would put torch and FAISS into this module's import graph, which is
    the one thing the file is organised around avoiding. The structural
    requirements are exactly the two retrieval entry points named below.

    This duplicates about a dozen lines of ``src.models.retriever.SASRecRetriever``
    — the dismissal/seed bookkeeping — rather than delegating to it, for one
    reason: the offline adapter reports ``contribution=0.0`` for every candidate
    because the model it wraps returns ids without scores, and the sidecar's job
    is to put a real retrieval score into the audit. The two should converge on
    one implementation the moment the scored retrieval method lands (see
    ``SCORED_RETRIEVAL_METHOD``); until then, converging early would mean
    deleting the score path before it has anything to consume.
    """

    model: Any
    vocabulary: tuple[int, ...]
    max_sequence_length: int
    cold_start_threshold: int | None
    fastpath_guard_source: str

    @property
    def family(self) -> str:
        return RETRIEVER_FAMILY_SASREC

    def retrieve(
        self,
        positive_history_movie_ids: list[int],
        *,
        limit: int,
        excluded_movie_ids: Iterable[int] = (),
        dismissed_movie_ids: Iterable[int] = (),
    ) -> CandidateRetrieval:
        excluded = set(excluded_movie_ids)
        dismissed = set(dismissed_movie_ids)
        hidden = excluded | dismissed
        # Only a dismissal drops a seed. The exclusion set contains the user's
        # own watched history, so treating it as a seed filter would empty
        # retrieval for exactly the users who have the most history (ADR 0012).
        seeds = [movie_id for movie_id in positive_history_movie_ids if movie_id not in dismissed]
        if limit <= 0 or not seeds:
            # An empty seed set is an ordinary online outcome — a user whose
            # whole history was dismissed — and ``CandidateIndex`` answers it
            # with an empty retrieval. ``recommend_from_history`` raises on it,
            # so absorb the difference here rather than let two conforming
            # retrievers disagree about what "no seeds" means.
            return CandidateRetrieval(contributions=(), seed_count=0, excluded_count=len(hidden))

        window = _encoder_window(seeds, self.max_sequence_length)
        scored = _scored_retrieval(self.model, window, limit, hidden)
        contributions = tuple(
            CandidateContribution(
                movie_id=movie_id,
                source=RETRIEVER_FAMILY_SASREC,
                # SASRec encodes the whole window into one query vector, so no
                # candidate is attributable to a single seed. ``None`` is the
                # honest answer; inventing one would put a fabricated "because
                # you watched…" into the prediction audit.
                seed_movie_id=None,
                contribution=score,
            )
            for movie_id, score in scored
        )
        # Only the seeds inside the encoder window drove the query, so a longer
        # history must not be counted whole, and a retrieval that returned
        # nothing was driven by none of them.
        return CandidateRetrieval(
            contributions=contributions,
            seed_count=len(window) if contributions else 0,
            excluded_count=len(hidden),
        )

    def fill_order(self) -> Sequence[int]:
        """No fill order — a SASRec bundle does not publish one.

        Not an oversight and not a stub. ``SASRecModel`` carries a
        ``PopularityModel``, but ``export_sasrec`` deliberately does not write it
        into the archive, so a bundle loaded from disk has an unfitted one; the
        offline ranker run injects item-item's popularity model by hand
        (``src/training/sasrec_ranker.py``), and the sidecar has no item-item
        model to take one from. The alternatives were to invent an order out of
        the item vocabulary — which is sorted movie-id order, not popularity, and
        would put a false ``popularity-fill`` label into the audit — or to return
        nothing and let a short retrieval be visibly short. The second is the
        honest one. Publishing a popularity artifact alongside the encoder is the
        fix, and it belongs to the training lane.
        """
        return ()

    def warmup_seed_movie_ids(self, count: int) -> list[int]:
        """The lowest ids in the encoder's own vocabulary, sorted.

        The same rule the item-item adapter uses, for the same reason: the warm
        input must be a pure function of the bundle. Vocabulary items rather than
        arbitrary ids so the warm rank exercises real embeddings — an all-unknown
        history encodes fine and proves less.
        """
        return sorted(self.vocabulary)[:count]


def serves_from_learned_path(*, history_size: int, cold_start_threshold: int | None) -> bool:
    """Which ranking route a request takes: learned, or the incumbent fallback.

    The rule is ``src.models.candidates.routing.learned_path_serves``, which this
    restates in three lines rather than importing. Importing it would pull
    ``src/models/candidates/__init__.py``, which imports ``.cf``, which imports
    implicit — a fit-time-only library the sidecar image does not install. #156
    moved the retrieval protocols out of that package for exactly this reason;
    moving ``routing.py`` out too is the follow-up that would let this delegate
    instead of restate.

    ``None`` is a real setting and not a missing one: it is how a bundle says it
    answers every request from the learned route. That is also what every bundle
    published before schema 2 means, since none of them declared a threshold —
    which is what keeps the incumbent path exactly where it was.
    """
    if cold_start_threshold is None:
        return True
    return history_size >= cold_start_threshold


def _encoder_window(newest_first_seeds: list[int], max_sequence_length: int) -> list[int]:
    """Turn the request's history into the order the encoder was trained on.

    **These two orders are opposite, and nothing before this fails when they are
    confused.** The coordinator builds ``positive_history_movie_ids`` most
    recently watched first (``src/serving/recommendations.py`` sorts descending
    by event rank), because that is the order item-item wants: its "because you
    watched…" attribution takes the first seed to reach a candidate, so newest
    first makes that the most recent title. SASRec is the reverse. It is trained
    on chronological ascending sequences (``build_user_history``,
    ``build_strict_prefix_examples``), the artifact contract records
    ``sequence_order: "oldest-to-newest"``, and ``_sequence_tensor`` left-pads so
    the *last* element is what the final position — the only vector retrieval
    reads — represents.

    Handing the wire order straight through would therefore do two silent things:
    encode the user's oldest title as their most recent, and, for anyone with
    more than ``max_sequence_length`` titles, truncate to the oldest window
    instead of the newest. Neither raises, and both would just look like a model
    that retrieves worse in production than it did offline.

    Truncation happens here rather than being left to the model so the slice is
    taken on the correct end: the newest ``max_sequence_length`` titles, in
    ascending order.
    """
    return list(reversed(newest_first_seeds[:max_sequence_length]))


# The method ``SASRecModel`` is expected to grow: ordered ``(movie_id, score)``
# pairs from the same exact FAISS search ``recommend_from_history`` runs, with
# the same exclusion semantics. Until it exists, retrieval scores are all zero
# and ``tests/unit/test_sidecar_sasrec_load.py`` holds a strict-xfail tripwire
# that turns into a failure the moment the method appears — which is the signal
# to delete the unscored branch below, not to delete the test.
SCORED_RETRIEVAL_METHOD = "recommend_from_history_scored"


def _scored_retrieval(
    model: Any,
    seeds: list[int],
    limit: int,
    hidden: set[int],
) -> list[tuple[int, float]]:
    """Ordered ``(movie_id, score)`` candidates, scored if the model can score."""
    scored = getattr(model, SCORED_RETRIEVAL_METHOD, None)
    if callable(scored):
        pairs: list[tuple[int, float]] = [
            (int(movie_id), float(score))
            for movie_id, score in scored(seeds, limit, excluded_movie_ids=hidden)
        ]
        return pairs
    movie_ids: list[int] = model.recommend_from_history(seeds, limit, excluded_movie_ids=hidden)
    # 0.0 is a placeholder, not a measurement. Do not compare this field across
    # families, and do not read it as "the model was indifferent".
    return [(int(movie_id), 0.0) for movie_id in movie_ids]


@dataclass(frozen=True)
class TopUp:
    """The result of topping a short retrieval up to the requested width."""

    retrieval: CandidateRetrieval
    filled: int
    shortfall: int = field(default=0)


def top_up_to_limit(
    retrieval: CandidateRetrieval,
    *,
    limit: int,
    fill_order: Sequence[int],
    blocked: set[int],
) -> TopUp:
    """Pad a retrieval out to ``limit`` from the bundle's fill order.

    **This is the sidecar's job, not a retriever's.** Two retrievers that both
    satisfy the contract currently hand the ranker different-sized sets —
    ``CandidateIndex`` pads from popularity internally and ``SASRecSidecarRetriever``
    cannot — and the ranker is entitled to one answer about how wide its input
    is. Putting the padding here rather than inside each family buys three
    things. It is written once, so a new family cannot get the ``blocked`` set
    subtly wrong and leak a watched title into a response. The
    ``popularity-fill`` label in the prediction audit is owned by the same code
    that owns every other source label. And it keeps "what does this model think
    is relevant" — which is what an offline recall@k measures — separate from
    "how wide is this response", which is a serving guarantee about coverage
    that no offline metric should be inflated by.

    The honest cost: for item-item this is provably a no-op, because
    ``CandidateIndex.retrieve`` already filled from the same order before
    returning. The function short-circuits on a full result precisely so that
    stays true bit-for-bit. So the mechanism is in the right place and is,
    today, load-bearing for nobody — SASRec publishes no fill order either. What
    it does earn now is visibility: a short retrieval is reported as a shortfall
    instead of being silently narrower than the caller asked for.
    """
    contributions = list(retrieval.contributions)
    if len(contributions) >= limit:
        return TopUp(retrieval=retrieval, filled=0)
    selected = {contribution.movie_id for contribution in contributions}
    filled = 0
    for movie_id in fill_order:
        if len(contributions) >= limit:
            break
        if movie_id in blocked or movie_id in selected:
            continue
        contributions.append(
            CandidateContribution(
                movie_id=movie_id,
                source=CANDIDATE_SOURCE_POPULARITY_FILL,
                seed_movie_id=None,
                contribution=0.0,
            )
        )
        selected.add(movie_id)
        filled += 1
    if filled == 0:
        # Nothing was added, so hand back the identical object rather than an
        # equal copy. Cheap, and it makes "the top-up did nothing" checkable.
        return TopUp(retrieval=retrieval, filled=0, shortfall=limit - len(contributions))
    return TopUp(
        retrieval=CandidateRetrieval(
            contributions=tuple(contributions),
            seed_count=retrieval.seed_count,
            excluded_count=retrieval.excluded_count,
        ),
        filled=filled,
        shortfall=limit - len(contributions),
    )


def _resolve_fastpath_guard() -> str:
    """Obtain — never duplicate — the fix for the fused-attention NaN defect.

    In ``eval()`` mode PyTorch's fused multi-head-attention fastpath returns NaN
    for a query position whose whole key row is masked. A SASRec history shorter
    than ``max_sequence_length`` is left-padded, so those rows exist for every
    real user, and the corruption is **depth-dependent**: at one encoder block it
    stays confined to the padded rows, and at two — ADR 0016's configuration —
    the padded row feeds the second block, the causal mask lets the last position
    attend over it, and the NaN lands in exactly the vector retrieval reads.
    Measured on torch 2.12.0 against this repo's ``SASRecEncoder``: at
    ``num_blocks=2`` every history length from 1 to 49 encodes to NaN and length
    50 — a full window, no padding — is clean. A single-layer regression test
    would pass and prove nothing.

    The one-line fix is ``torch.backends.mha.set_fastpath_enabled(False)``, and
    it belongs in the shared encoder path where training, evaluation and serving
    all inherit it — that work (O-9/W17) is not on ``main`` yet. This function
    therefore *depends* on it rather than reimplementing it: a second copy of a
    global toggle is how the two paths end up disagreeing about which numbers
    the published metrics were measured under.

    Two shapes of that fix are accepted, because the choice is not this lane's
    to make:

    * a callable ``disable_attention_fastpath`` exported from the shared module,
      which is called here; or
    * the module disabling the fastpath as an import side effect, which is
      detected by reading the flag back after importing it.

    Anything else is a named startup failure. Note that this resolves the
    *dependency*; ``_assert_encoder_is_finite`` is what proves it worked.
    """
    import torch

    from src.models.candidates import sasrec as shared_encoder

    guard = getattr(shared_encoder, FASTPATH_GUARD_SYMBOL, None)
    if callable(guard):
        guard()
        return GUARD_SOURCE_HOOK
    if not torch.backends.mha.get_fastpath_enabled():
        return GUARD_SOURCE_IMPORT
    raise AttentionFastpathGuardUnavailableError(
        f"{SHARED_ENCODER_MODULE} does not disable PyTorch's fused attention fastpath, so this "
        "encoder would return NaN for every history shorter than its sequence window and "
        "retrieve nothing. Land O-9/W17 first: the shared encoder module must either "
        f"export a callable {FASTPATH_GUARD_SYMBOL}() or disable the fastpath on import "
        "(torch.backends.mha.set_fastpath_enabled(False)). Refusing to serve a SASRec bundle "
        "until it does."
    )


def _assert_encoder_is_finite(model: Any, vocabulary: tuple[int, ...]) -> None:
    """Prove the loaded encoder represents padded histories, at the configured depth.

    Behavioural rather than a version check, because the guard above establishes
    that the fix is *present* and this establishes that it *worked* — against
    whichever torch the image resolved, at whichever ``num_blocks`` the bundle
    was trained with. A torch upgrade that reintroduces the defect under a
    different flag fails here, at boot, rather than in production.

    Probed at the two lengths that matter and no more: 1, the shortest history
    that can reach the encoder, and ``max_sequence_length - 1``, the longest one
    that is still padded. The defect is uniform across padded lengths — every
    length from 1 to 49 fails together — so a wider sweep costs forward passes
    and proves nothing extra. The unpadded control at exactly
    ``max_sequence_length`` is clean even when the bug is live, which is why it
    is a fixture assertion and not a boot check.
    """
    import torch

    max_length = int(model.config.max_sequence_length)
    lengths = sorted({1, max(1, max_length - 1)})
    for length in lengths:
        # Real vocabulary items, cycled — an all-unknown history encodes fine
        # and would prove less than one built from trained embeddings.
        history = [vocabulary[index % len(vocabulary)] for index in range(length)]
        encoded = model.encode_movie_history(history)
        if not bool(torch.isfinite(encoded).all()):
            raise EncoderProducesNonFiniteVectorsError(
                f"the loaded SASRec encoder returned a non-finite query vector for a "
                f"{length}-item history against a {max_length}-item window. This is the fused "
                "attention fastpath defect and it means retrieval would return no candidates at "
                "all for that user; the guard resolved but did not take effect."
            )
        # The end-to-end half of the same claim: a finite query vector that still
        # retrieves nothing would be a different fault with the same symptom.
        if not model.recommend_from_history(history, 1):
            raise EncoderProducesNonFiniteVectorsError(
                f"the loaded SASRec encoder retrieved no candidate at all for a {length}-item "
                f"history against a {max_length}-item window, so no user below the window would "
                "be served by retrieval."
            )


def load_sequence_retriever(retriever: RetrieverRef, artifact_dir: Path) -> SASRecSidecarRetriever:
    """Rebuild a SASRec retriever from a published bundle, or refuse to.

    Every failure here reaches ``lifespan`` and kills the worker before it can
    accept a request. That is deliberate and it is the ADR 0013 rollback story:
    a bundle this build cannot fully realise must stop the deployment, not
    degrade it, because a sidecar that boots and answers with something else
    produces audit rows under a model version that never served them.

    The file layout this expects is the one ``src/models/candidates/sasrec_artifact.py``
    writes: a deterministic ``sasrec-model.zip`` beside a ``sasrec-manifest.json``
    that pins its checksum, its vocabulary fingerprint and its config. The serving
    manifest's ``encoder`` role must name that archive; the loader cross-checks
    the two manifests against each other rather than trusting either alone, and
    refuses a layout it does not recognise instead of guessing at one.
    """
    guard_source = _resolve_fastpath_guard()

    from src.models.candidates.sasrec_artifact import (
        MANIFEST_FILENAME,
        SASRecArtifactManifest,
        load_sasrec,
    )

    encoder = retriever.artifacts.get("encoder")
    if encoder is None:
        raise SequenceBundleIncompleteError(
            f"retriever family {RETRIEVER_FAMILY_SASREC!r} declares no 'encoder' artifact"
        )
    artifact_manifest_path = artifact_dir / MANIFEST_FILENAME
    if not artifact_manifest_path.is_file():
        raise SequenceBundleIncompleteError(
            f"a {RETRIEVER_FAMILY_SASREC!r} bundle must ship {MANIFEST_FILENAME} beside "
            f"{encoder.filename}; the encoder archive alone does not carry the checksums the "
            "loader validates it against"
        )
    artifact_manifest = SASRecArtifactManifest.load(artifact_manifest_path)
    if artifact_manifest.model_filename != encoder.filename:
        # Two manifests naming different files is a publishing bug, and it is the
        # one that would otherwise load a *different* encoder than the serving
        # manifest pinned the checksum of.
        raise SequenceBundleIncompleteError(
            f"the serving manifest pins encoder {encoder.filename!r} but {MANIFEST_FILENAME} "
            f"describes {artifact_manifest.model_filename!r}"
        )

    model = load_sasrec(artifact_manifest_path)
    _assert_declared_params_match(retriever, model, artifact_manifest.max_sequence_length)
    vocabulary = _vocabulary_of(model)
    _assert_encoder_is_finite(model, vocabulary)

    logger.info(
        "sasrec_retriever_loaded encoder=%s items=%s window=%s cold_start_threshold=%s "
        "index_type=%s fastpath_guard=%s",
        encoder.version,
        len(vocabulary),
        artifact_manifest.max_sequence_length,
        model.cold_start_threshold,
        INDEX_TYPE_FLAT_IP_EXACT,
        guard_source,
    )
    return SASRecSidecarRetriever(
        model=model,
        vocabulary=vocabulary,
        max_sequence_length=artifact_manifest.max_sequence_length,
        cold_start_threshold=model.cold_start_threshold,
        fastpath_guard_source=guard_source,
    )


def _assert_declared_params_match(
    retriever: RetrieverRef, model: Any, artifact_max_sequence_length: int
) -> None:
    """Hold the reconstructed encoder to what the serving manifest promised.

    ``RetrieverRef.validate`` already checks that these parameters are *present*
    and well-formed. It cannot check that they are *true*, because at that point
    nothing has opened the archive. This is where the claim meets the artifact,
    and a disagreement is refused rather than resolved in either direction: the
    published metrics were measured under one of the two, and there is no way to
    tell which from here.
    """
    declared_window = int(retriever.params["max_sequence_length"])
    if declared_window != artifact_max_sequence_length:
        raise SequenceBundleIncompleteError(
            f"the serving manifest declares max_sequence_length {declared_window} but the "
            f"encoder was trained with {artifact_max_sequence_length}"
        )
    declared_threshold = retriever.params["cold_start_threshold"]
    if declared_threshold != model.cold_start_threshold:
        raise SequenceBundleIncompleteError(
            f"the serving manifest declares cold_start_threshold {declared_threshold!r} but the "
            f"encoder archive carries {model.cold_start_threshold!r}, so the two disagree about "
            "which users this bundle answers from retrieval"
        )
    # ``index_type`` is validated as a string by the manifest; this is the half
    # that can only be checked against the model. ``SASRecConfig.faiss_exact``
    # defaults to False, which builds an IVF index whose rebuild is not
    # deterministic — serving that under a manifest claiming exact search would
    # mean serving a retriever nobody measured.
    if not bool(model.config.faiss_exact):
        raise SequenceBundleIncompleteError(
            f"the serving manifest declares index_type {INDEX_TYPE_FLAT_IP_EXACT!r} but the "
            "encoder config has faiss_exact=False, so loading rebuilds an IVF index instead of "
            "the exact one the published retrieval numbers were measured under"
        )


def _vocabulary_of(model: Any) -> tuple[int, ...]:
    """The loaded encoder's item vocabulary, read once at load.

    Reaches into ``_index_to_item`` because ``SASRecModel`` exposes no public
    accessor and this module needs the vocabulary for two things that must be
    pure functions of the bundle — the warm-up seeds and the finiteness probe.
    Read once, here, rather than at every call site; ``sasrec_artifact.py``
    already reads the same attribute to export it.
    """
    index_to_item: dict[int, int] = model._index_to_item
    if not index_to_item:
        raise SequenceBundleIncompleteError(
            "the loaded SASRec encoder has an empty item vocabulary and can retrieve nothing"
        )
    return tuple(int(item_id) for item_id in index_to_item.values())


def sidecar_retriever_for(
    *,
    retriever: SidecarRetriever | None,
    candidates: CandidateIndex | None,
) -> SidecarRetriever:
    """The retrieval stage a loaded bundle serves from.

    A bundle carries a family-specific ``retriever`` when its loader built one;
    a schema 1 bundle (and every bundle constructed in a test that predates the
    split) carries only a ``CandidateIndex``, which is adapted here. Keeping the
    adaptation out of ``ServingArtifactBundle.__post_init__`` avoids an import
    cycle: this module imports ``src.models.artifacts``, and that module only
    reaches back into this one lazily, from ``load``.
    """
    if retriever is not None:
        return retriever
    if candidates is None:
        raise SequenceBundleIncompleteError(
            "the serving bundle carries neither a retriever nor a candidate index, so it has no "
            "retrieval stage to serve from"
        )
    return ItemItemSidecarRetriever(candidates)
