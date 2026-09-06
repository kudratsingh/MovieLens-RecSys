"""The policy string a warm request must answer with, read from the served bundle.

The k6 scripts assert every warm response's `serving_policy`, which is the right
strictness and was the wrong constant: they named `item-item-cosine+lightgbm`
directly, so the moment a tenant's champion is a different family the gate fails
every warm check and reports it as a latency verdict — when what actually
happened is that the assertion still described the previous champion.

This derives the same string from the manifest instead, so the check follows the
bundle under test. The coordinator composes the policy as the retriever family
plus the ranker's name, and this mirrors that composition rather than inventing a
second vocabulary.

Reading a *manifest* rather than a path is deliberate: the demo stack mounts a
volume over the image's baked bundle, so the file the sidecar actually opened is
the only honest source, and `run_gate.sh` pipes it in from the running container.

Every failure returns the incumbent default. A gate that cannot read a manifest
should behave exactly as it did before this module existed, not refuse to run —
the fallback is what makes this change safe to land ahead of the bundle it is for.
"""

from __future__ import annotations

import json
import sys
from typing import Any

#: What the composed policy has always been for the incumbent bundle, and the
#: answer whenever the manifest cannot be read.
DEFAULT_POLICY = "item-item-cosine+lightgbm"

#: The ranker half. Both routes of a schema 2 bundle are LightGBM boosters, so
#: the ranker does not vary the way the retriever does.
RANKER_NAME = "lightgbm"


def retriever_family(manifest: Any) -> str:
    """The family name, from either manifest schema.

    Schema 2 names it outright. Schema 1 predates the field, but its candidate
    artifact type is the same string — `item-item-cosine` — which is why the
    fallback is a real reading rather than a guess.
    """
    if not isinstance(manifest, dict):
        return "item-item-cosine"
    retriever = manifest.get("retriever")
    if isinstance(retriever, dict):
        family = retriever.get("family")
        if isinstance(family, str) and family:
            return family
    candidate = manifest.get("candidate")
    if isinstance(candidate, dict):
        artifact_type = candidate.get("artifact_type")
        if isinstance(artifact_type, str) and artifact_type:
            return artifact_type
    return "item-item-cosine"


def expected_policy(raw: str) -> str:
    """Compose the policy from a manifest document, or fall back to the default."""
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return DEFAULT_POLICY
    return f"{retriever_family(manifest)}+{RANKER_NAME}"


def main() -> None:
    print(expected_policy(sys.stdin.read()))


if __name__ == "__main__":
    main()
