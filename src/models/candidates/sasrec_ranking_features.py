"""The two point-in-time SASRec ranker features of ADR 0018 increment 1.

The ranker has always seen eight aggregate columns — three user counters, three
popularity windows, item age, a genre affinity. Every one of them summarises the
history, and none can say *which* past title makes this candidate plausible,
which is the question a top-ten ordering turns on. This module computes the two
scalars that can:

``sasrec_user_item_score``
    The inner product of the L2-normalised user vector and the L2-normalised
    candidate item embedding. Both sides are normalised at the retrieval
    boundary, so this is the cosine and it is *exactly* the quantity FAISS
    ranked the candidate on.

``sasrec_user_item_logit``
    The same inner product over the unnormalised representations — the quantity
    the BCE-family objective calibrated, carrying the magnitude the normalised
    score discards. Two candidates can share a cosine and differ here.

Three properties are load-bearing and are why this is a module rather than two
lines at the call site.

**One encode.** The user vector comes from ``SASRecModel.encode_histories`` /
``encode_dense_history``, which return both representations from a single
``encode_positions`` pass — the same pass retrieval uses. The feature is
therefore the score that ordered the candidate, not a re-derivation of it that
happens to agree today.

**One slice.** The caller passes the strict prefix it also used to retrieve and
to build the #126 exclusion set. Nothing in here re-derives "what had this user
seen by then", because two derivations are two chances to disagree.

**Explicit missingness.** A row with no encodable point-in-time history, or a
candidate outside the encoder's vocabulary, gets NaN rather than a fabricated
number. LightGBM has first-class missing handling and learns a default direction
per split, so "this row is the other route" becomes usable information instead of
a value that pretends to be a similarity. ADR 0018 lists this under consequences:
missingness becomes a signal whose effect must be reported, not absorbed.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from src.feature_contract import SASREC_SCORE_COLUMNS

from .sasrec import SASRecModel

#: Rows and candidates the encoder cannot speak about. See the module docstring.
MISSING = float("nan")


class SasrecScoreFeatures:
    """Scores candidates against an already-encoded user vector.

    Constructed once per run: it materialises the item embedding matrices, which
    are ~34k x 64 floats twice over for the pinned artifact — small enough to
    hold, far too expensive to rebuild per group.
    """

    def __init__(self, model: SASRecModel) -> None:
        self._model = model
        self._normalized_items, self._unnormalized_items = model.item_matrices()

    @property
    def n_items(self) -> int:
        return int(self._normalized_items.shape[0])

    def dense_indices(self, movie_ids: Sequence[int]) -> np.ndarray:
        """Dense encoder ids for movie ids; ``-1`` where the item is unknown.

        ``-1`` rather than a raise: a subsampled smoke run splits at its own
        cutoff and can legitimately hold an item the pinned model never saw, and
        the honest answer for such a candidate is "no opinion", not a crash.
        """
        return np.fromiter(
            (
                dense if (dense := self._model.dense_index_for(int(movie_id))) is not None else -1
                for movie_id in movie_ids
            ),
            dtype=np.int64,
            count=len(movie_ids),
        )

    def scores_for(
        self,
        normalized_user: np.ndarray | None,
        unnormalized_user: np.ndarray | None,
        movie_ids: Sequence[int],
    ) -> tuple[np.ndarray, np.ndarray]:
        """``(score, logit)`` for one user vector against a list of candidates.

        ``None`` for either vector means the row has no encodable history — a
        fallback-route positive — and every candidate takes the missing value.
        """
        n = len(movie_ids)
        score = np.full(n, MISSING, dtype=np.float64)
        logit = np.full(n, MISSING, dtype=np.float64)
        if normalized_user is None or unnormalized_user is None or n == 0:
            return score, logit

        dense = self.dense_indices(movie_ids)
        known = dense >= 1
        if not known.any():
            return score, logit
        rows = dense[known] - 1
        score[known] = self._normalized_items[rows] @ np.asarray(normalized_user)
        logit[known] = self._unnormalized_items[rows] @ np.asarray(unnormalized_user)
        return score, logit

    def frame_for(
        self,
        normalized_user: np.ndarray | None,
        unnormalized_user: np.ndarray | None,
        movie_ids: Sequence[int],
    ) -> pd.DataFrame:
        """The two columns, named and ordered as the learned-route contract."""
        score, logit = self.scores_for(normalized_user, unnormalized_user, movie_ids)
        return pd.DataFrame(
            {SASREC_SCORE_COLUMNS[0]: score, SASREC_SCORE_COLUMNS[1]: logit},
        )


def missing_frame(n_rows: int) -> pd.DataFrame:
    """The two columns, all missing — the fallback route's shape."""
    return pd.DataFrame(
        {
            SASREC_SCORE_COLUMNS[0]: np.full(n_rows, MISSING, dtype=np.float64),
            SASREC_SCORE_COLUMNS[1]: np.full(n_rows, MISSING, dtype=np.float64),
        }
    )
