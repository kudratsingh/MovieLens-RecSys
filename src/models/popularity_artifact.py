"""The popularity fill order a bundle publishes, as deterministic bytes.

A retriever that cannot reach ``limit`` candidates after exclusions has to top
up from somewhere, and the only honest somewhere is the popularity ordering the
offline evaluation used. Item-item carries one inside ``CandidateIndex``; a
SASRec bundle had none, so ``SASRecSidecarRetriever.fill_order()`` returned
nothing rather than invent an order and write a false ``popularity-fill`` label
into the prediction audit. This module is the missing half: one file, one
ordering, one checksum.

**Why it lives here and not in ``src/models/candidates/``.** Importing anything
from that package runs its ``__init__``, which imports ``implicit`` — a
fit-time-only library the model sidecar does not install (the same packaging
defect #156 hit with the retrieval protocols). The reader has to work in an
image that has neither implicit nor pandas, so nothing above stdlib is imported
here and the derivation takes a plain mapping of counts rather than a frame.

**Why the tiebreak is stated rather than inherited.** ``PopularityModel.fit``
orders with ``sort_values(ascending=False)``, whose default kind is quicksort
and therefore not stable: which of two equally-rated movies comes first is an
implementation detail of pandas, and it can move between versions. That is fine
for a model whose output is read as a ranking, and not fine for bytes a manifest
pins by SHA-256 — a bundle whose recorded checksum stops matching its own file
after a dependency bump is exactly the failure the v2 manifest exists to catch.
So the order here is fully specified: interaction count descending, then
``movieId`` ascending. It is the same key ``src/training/sasrec.py`` already
uses to rank popularity for its retrieval diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

# The role a serving manifest gives this artifact, and the name it takes inside
# a bundle. The sidecar reads the file by fixed name, so the name is part of the
# contract rather than a detail of whoever wrote it.
POPULARITY_ARTIFACT_FILENAME = "popularity-order.json"
POPULARITY_ARTIFACT_TYPE = "popularity-order"
POPULARITY_ARTIFACT_SCHEMA_VERSION = 1


def popularity_order_from_counts(counts: Mapping[int, int]) -> tuple[int, ...]:
    """Movie ids most-popular-first, with ties broken by ascending movie id.

    ``counts`` is interaction count per movie over the training frame — the
    quantity ``PopularityModel`` ranks on, passed as a mapping so this module
    stays free of pandas.
    """
    return tuple(sorted(counts, key=lambda movie_id: (-counts[movie_id], movie_id)))


def serialize_popularity_order(movie_ids: Iterable[int]) -> bytes:
    """The canonical bytes for an ordering: sorted keys, no trailing whitespace.

    Deliberately holds nothing but the schema version and the ids. Provenance —
    which run, which cutoff, which threshold, which machine — belongs in the
    serving manifest's ``Lineage``, because anything in here is inside the hash
    and would make the artifact unreproducible from its documented inputs.
    """
    order = [int(movie_id) for movie_id in movie_ids]
    if len(order) != len(set(order)):
        raise ValueError("popularity order contains duplicate movie ids")
    payload = {
        "schema_version": POPULARITY_ARTIFACT_SCHEMA_VERSION,
        "movie_ids": order,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def popularity_order_sha256(movie_ids: Iterable[int]) -> str:
    return hashlib.sha256(serialize_popularity_order(movie_ids)).hexdigest()


def write_popularity_order(path: Path, movie_ids: Sequence[int]) -> str:
    """Write the artifact create-only and return its SHA-256.

    Create-only for the same reason the SASRec exporter is: an artifact
    directory names a run, and a run's bytes are not something a later invocation
    gets to change underneath a manifest that already pinned them.
    """
    data = serialize_popularity_order(movie_ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}") from None
    return hashlib.sha256(data).hexdigest()


def read_popularity_order(path: Path, *, expected_sha256: str | None = None) -> tuple[int, ...]:
    """Read an ordering back, refusing anything that is not exactly what was pinned.

    ``expected_sha256`` is checked against the raw bytes rather than against a
    re-serialization of the parsed payload: the point is to prove this is the
    file the manifest was written against, and a re-serialization would happily
    agree with a file whose whitespace or key order had drifted.
    """
    data = path.read_bytes()
    if expected_sha256 is not None:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise ValueError(
                f"popularity order checksum mismatch for {path.name}: "
                f"expected {expected_sha256}, got {actual}"
            )
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path.name} is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain an object")
    schema_version = payload.get("schema_version")
    if schema_version != POPULARITY_ARTIFACT_SCHEMA_VERSION:
        raise ValueError(f"unsupported popularity order schema {schema_version!r}")
    movie_ids = payload.get("movie_ids")
    if not isinstance(movie_ids, list) or not all(
        isinstance(movie_id, int) and not isinstance(movie_id, bool) for movie_id in movie_ids
    ):
        raise ValueError(f"{path.name} movie_ids must be an integer list")
    if len(movie_ids) != len(set(movie_ids)):
        raise ValueError(f"{path.name} movie_ids contains duplicates")
    return tuple(movie_ids)
