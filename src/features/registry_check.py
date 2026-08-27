"""Semantic comparison of a Feast registry against the definitions in this tree.

The features image bakes its registry: ``feast apply`` runs at build time and
writes ``src/features/feast_repo/data/registry.db`` into the image, so the
sidecars need no shared volume for it. That only holds if something proves the
baked registry still describes the feature views the image's code declares.

The comparison is deliberately semantic and never a byte hash.
``FeatureStore.apply`` writes a protobuf carrying ``last_updated``, so two
applies of byte-identical definitions produce different bytes; a checksum gate
would fail every build and every release for no reason. What is compared is the
meaning: project, entity names and join keys, feature-view names and their
entities, TTLs, source table and timestamp field, and each view's field names
and dtypes.

``describe_registry`` and ``describe_declared_definitions`` return the same
shape, so the release path can also compare the registry an apply *would*
produce against the one baked into the running image — the pre-deploy container
is thrown away, so a divergence there means the sidecar is serving definitions
nobody applied.

Nothing here constructs ``Settings``: this runs during ``docker build``, where
the production credential guards would refuse to construct one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any

import yaml
from feast import Entity, FeatureStore, FeatureView

DEFAULT_REPO_PATH = Path("src/features/feast_repo")
DECLARATIONS_MODULE = "src.features.feast_repo.features"

RegistryDescription = dict[str, Any]


class RegistryCheckError(RuntimeError):
    """The registry could not be described at all — a different failure from drift."""


def _describe_entities(entities: Iterable[Entity]) -> dict[str, Any]:
    return {
        entity.name: {
            "join_key": entity.join_key,
            "value_type": entity.value_type.name,
        }
        for entity in entities
    }


def _describe_feature_views(
    feature_views: Iterable[FeatureView],
    *,
    join_keys: frozenset[str],
) -> dict[str, Any]:
    """Describe each view, minus the entity columns ``apply`` injects.

    A declared ``FeatureView.schema`` holds only the feature fields, while the
    registry round-trip adds a column per join key. Dropping the join keys from
    both sides is what lets one description compare a declaration with an
    applied registry; the entities themselves are still compared by name.
    """
    described: dict[str, Any] = {}
    for view in feature_views:
        # A feature view with no batch source cannot be materialized at all;
        # describing it as absent keeps that visible in a diff rather than
        # raising from inside a comparison.
        source = view.batch_source
        described[view.name] = {
            "entities": sorted(view.entities),
            "ttl_seconds": None if view.ttl is None else int(view.ttl.total_seconds()),
            "source_name": None if source is None else source.name,
            "source_table": None if source is None else str(getattr(source, "table", "")),
            "timestamp_field": None if source is None else source.timestamp_field,
            "fields": {
                field.name: str(field.dtype)
                for field in sorted(view.schema, key=lambda field: field.name)
                if field.name not in join_keys
            },
        }
    return described


def describe(
    *,
    project: str,
    entities: Iterable[Entity],
    feature_views: Iterable[FeatureView],
) -> RegistryDescription:
    """Normalize one set of Feast objects into a comparable description."""
    entity_list = list(entities)
    join_keys = frozenset(entity.join_key for entity in entity_list)
    return {
        "project": project,
        "entities": _describe_entities(entity_list),
        "feature_views": _describe_feature_views(feature_views, join_keys=join_keys),
    }


def repo_config(repo_path: Path) -> dict[str, Any]:
    config_path = repo_path / "feature_store.yaml"
    if not config_path.is_file():
        raise RegistryCheckError(f"no feature_store.yaml under {repo_path}")
    # The file interpolates ${FEAST_POSTGRES_*} and ${REDIS_CONNECTION_STRING};
    # to YAML those are ordinary strings, so this read needs no environment.
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RegistryCheckError(f"{config_path} does not contain a mapping")
    return loaded


def registry_file(repo_path: Path) -> Path | None:
    """The registry file this repo config points at, if it is a file registry."""
    registry = repo_config(repo_path).get("registry")
    if not isinstance(registry, str):
        return None
    return repo_path / registry


def describe_registry(repo_path: Path = DEFAULT_REPO_PATH) -> RegistryDescription:
    """Describe the registry a Feast repo currently has on disk.

    Reads the registry file only. Feast resolves the offline and online store
    configs when the ``FeatureStore`` is constructed but connects to neither to
    list objects, which is what makes this runnable inside ``docker build``.
    """
    path = registry_file(repo_path)
    if path is not None and not path.is_file():
        raise RegistryCheckError(
            f"no Feast registry at {path}; run `feast -c {repo_path} apply` first"
        )
    try:
        store = FeatureStore(repo_path=str(repo_path))
        # Both listings are scoped to the config's project, so a registry
        # written under a different project name surfaces here as an empty
        # feature-view set rather than as a silently passing comparison.
        return describe(
            project=store.project,
            entities=store.list_entities(),
            feature_views=store.list_feature_views(),
        )
    except Exception as error:
        # Feast raises validation errors, protobuf errors and its own exception
        # types from this path. They all mean the same thing to a caller —
        # the registry could not be read — and that is a different outcome
        # from "it was read and it disagrees", which is what the exit codes
        # keep apart.
        raise RegistryCheckError(
            f"could not read the Feast registry at {repo_path}: {error}"
        ) from error


def describe_declared_definitions(*, project: str) -> RegistryDescription:
    """Describe the entities and feature views ``features.py`` declares.

    The module is scanned rather than enumerated so a feature view added to the
    repo is compared automatically — the same way ``feast apply`` finds it.
    """
    module = import_module(DECLARATIONS_MODULE)
    declared = list(vars(module).values())
    return describe(
        project=project,
        entities=[value for value in declared if isinstance(value, Entity)],
        feature_views=[value for value in declared if isinstance(value, FeatureView)],
    )


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, nested in value.items():
            flattened.update(_flatten(nested, f"{prefix}.{key}" if prefix else str(key)))
        return flattened
    return {prefix: value}


def registry_differences(
    actual: RegistryDescription,
    expected: RegistryDescription,
) -> list[str]:
    """Dotted paths on which two descriptions disagree, including absences.

    A path present on one side only is a difference, so a feature view that was
    dropped or renamed is reported rather than skipped.
    """
    left = _flatten(actual)
    right = _flatten(expected)
    return sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))


def check_registry(repo_path: Path = DEFAULT_REPO_PATH) -> list[str]:
    """Compare the on-disk registry against this tree's declarations."""
    described = describe_registry(repo_path)
    project = described["project"]
    return registry_differences(described, describe_declared_definitions(project=project))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Assert a Feast registry semantically matches the feature "
            "definitions declared in this tree."
        )
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=DEFAULT_REPO_PATH,
        help="Feast repo directory holding feature_store.yaml (default: %(default)s).",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_description",
        help=(
            "Print the registry's description as JSON instead of checking it. "
            "Use it to record the baked registry before a release re-applies."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.print_description:
            json.dump(describe_registry(args.repo_path), sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0
        differences = check_registry(args.repo_path)
    except RegistryCheckError as error:
        print(f"feast registry check could not run: {error}", file=sys.stderr)
        return 2
    if differences:
        print(
            f"the Feast registry in {args.repo_path} does not match the declared "
            f"feature definitions; re-run `feast -c {args.repo_path} apply`. "
            f"Differing paths:",
            file=sys.stderr,
        )
        for path in differences:
            print(f"  - {path}", file=sys.stderr)
        return 1
    print(f"feast registry in {args.repo_path} matches the declared feature definitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
