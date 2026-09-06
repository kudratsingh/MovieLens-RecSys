"""Candidate-generation families.

The two names below are re-exported lazily, and that is load-bearing rather
than stylistic. `cf` imports `implicit`, which is an ALS *training* library:
the model sidecar never runs ALS, so `infra/features/requirements.txt`
deliberately does not ship it. Importing it eagerly here meant that merely
touching this package — which `src/serving/sequence_retrieval.py` does to
reach the shared SASRec encoder — raised `ModuleNotFoundError` inside the
sidecar image, and that module's contract is to kill the worker rather than
serve from a half-loaded bundle. The result was that a SASRec bundle could
not boot in the image at all, while every test passed on a developer machine
where `implicit` happens to be installed.

PEP 562 lazy attribute access keeps `from src.models.candidates import CFModel`
working for the training code that wants it, without making the serving path
pay for a dependency it does not have.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .cf import CFModel
    from .popularity import PopularityModel

__all__ = ["CFModel", "PopularityModel"]

_LAZY_ATTRIBUTES = {"CFModel": ".cf", "PopularityModel": ".popularity"}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_ATTRIBUTES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    return getattr(import_module(module_name, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)
