"""Move one tenant's registered champion onto a serving bundle, by hand.

**This is not Phase 6's automatic routing gate, and it must not be mistaken for
one.** Phase 6 owns champion/challenger split, shadow deploys and the automated
promotion that follows an evaluation gate. This module is the manual step ADR
0001 has always assumed exists underneath that: an operator, holding a bundle
they can point at, moving one tenant's three champion columns in
``public.tenants`` from one verified set of coordinates to another. It decides
nothing about whether a model is *better*; the evaluation gate does that, and
running this without one is a choice the operator is making, not a judgement
this tool has made for them.

It exists because migration 0016 wrote the ``demo`` tenant's champion as three
literals and said, in its own docstring, that moving that row "is a promotion —
Phase 6's gate owns that". Nothing else in the tree could move it. That made a
published bundle unserveable: ``ModelRankingService.rank`` refuses any request
whose ``ChampionCoordinates`` do not match the manifest it loaded, so a sidecar
mounting a new bundle against an unmoved registry row answers every warm request
with ``policy=popularity, fallback_reason=champion-mismatch``. Fail-closed is
the right behaviour — it is what stops a deployment serving under a version
nobody registered — but with no way to register the new version, the gate that
should measure the bundle cannot run at all.

**What a promotion is, and what it is not.** It moves the *registry*, not the
image. The serving bundle is baked into the sidecar image (ADR 0013), so which
bytes a tenant is served by is an image decision; this row says which bytes that
tenant is *allowed* to be served by. The two have to agree, which is why the
order on a real deployment is: publish the bundle, roll the image that bakes it,
then promote. Promote first and every request falls back to popularity until the
image lands — audited, visible, and reversible, but a fallback.

**Verify before writing.** The manifest is loaded through
``ServingManifest.load``, which re-hashes every artifact the bundle declares and
re-runs every structural validator (family roles and parameters, the per-route
ranker feature contract against the booster files, the cross-route permutation
guard). A promotion that pinned a tenant to bytes nobody checked is the exact
failure the checksum pinning exists to prevent, so verification failure refuses
and changes nothing.

**Reverting.** Every promotion writes a JSON snapshot of the prior coordinates
to ``.release/champion/<tenant>.json`` *before* it writes the row, and
``--revert`` restores from it. That directory is where ``infra/deploy/deploy.sh``
already keeps ``current`` and ``previous``: per-host release state, gitignored,
and the first place anybody looks when a release has to be undone. Deliberately
not a database table — the snapshot's whole job is to survive a promotion that
went wrong, and keeping it in the row being overwritten is circular.

``--revert`` does not re-verify a bundle, because it must not need one on disk:
the coordinates it restores were verified when they were promoted, the sidecar
re-verifies the SHA-256s it loads on every boot, and requiring the old bundle to
still be mounted would block the one path that exists for an emergency.

**Propagation.** ``TenantRouter`` caches the row for
``DEFAULT_CACHE_TTL_SECONDS`` (30 s) per API process, and a CLI cannot reach
another process's cache. A promotion is therefore live within about half a
minute rather than instantly, which the summary says out loud so nobody reads
the intervening popularity answers as a failed promotion.

**Imports are lazy where the sibling modules' are**, for the same reason: the
slim API image ships no LightGBM, and ``src.models.artifacts`` imports it at
module scope to read a booster's recorded feature contract. So this module is
importable everywhere ``src.release`` is, and the bundle verification — the one
part that needs LightGBM — is imported inside the call. In practice it runs from
a checkout (``make promote``), which is where a bundle directory and the migrator
DSN are both in reach.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from src.config import Settings
from src.release.bootstrap import TREE_ROOT, ReleaseError
from src.serving.tenancy import TenantChampion

logger = logging.getLogger("release.promote")

# Per-host release state, beside `.release/current` and `.release/previous`
# (ADR 0013). One file per tenant so two tenants' promotions cannot overwrite
# each other's revert target, and named from the tenant id so the path is
# derivable rather than remembered.
DEFAULT_SNAPSHOT_DIR = TREE_ROOT / ".release" / "champion"
SNAPSHOT_SCHEMA_VERSION = 1

# The three columns migration 0016 added, in the order they are written and
# printed. `ck_tenants_champion_complete` allows exactly all-three or none, so
# they are only ever handled as a set.
CHAMPION_FIELDS: tuple[str, ...] = ("candidate_version", "ranker_version", "feature_version")

_TENANT_QUERY = text("""
    SELECT id,
           champion_candidate_version,
           champion_ranker_version,
           champion_feature_version,
           updated_at
    FROM public.tenants
    WHERE id = :tenant_id
    """)

# One statement for all three columns. A half-written champion is not something
# this tool has to be careful about avoiding — it is something it cannot express:
# either the statement commits and all three moved, or it does not and none did.
# `ck_tenants_champion_complete` is the database's own enforcement of the same
# rule, and it stays the enforcer of last resort (ADR 0008).
_PROMOTE_STATEMENT = text("""
    UPDATE public.tenants
    SET champion_candidate_version = :candidate_version,
        champion_ranker_version = :ranker_version,
        champion_feature_version = :feature_version
    WHERE id = :tenant_id
    """)


class PromotionError(ReleaseError):
    """A promotion could not be performed, and nothing was written."""


class PromotionRefusedError(PromotionError):
    """The promotion is well-formed but this run is not allowed to perform it."""


@dataclass(frozen=True)
class TenantRow:
    """What ``public.tenants`` currently says about one tenant's champion."""

    tenant_id: str
    champion: TenantChampion | None
    # Opaque on purpose: this module only ever asks whether it moved, and the
    # driver's answer is a timestamp on Postgres and whatever the test schema's
    # trigger writes elsewhere.
    updated_at: Any

    def describe(self) -> str:
        return self.champion.describe() if self.champion is not None else "none"


@dataclass(frozen=True)
class Snapshot:
    """The champion a tenant stood on before a promotion moved it."""

    tenant_id: str
    captured_at: str
    actor: str
    previous: TenantChampion | None
    promoted_to: TenantChampion
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "tenant_id": self.tenant_id,
            "captured_at": self.captured_at,
            "actor": self.actor,
            "source": self.source,
            "previous": _champion_to_dict(self.previous),
            "promoted_to": _champion_to_dict(self.promoted_to),
        }

    @classmethod
    def load(cls, path: Path) -> Snapshot:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PromotionError(
                f"the champion snapshot at {path} could not be read: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise PromotionError(f"the champion snapshot at {path} does not contain an object")
        schema_version = value.get("schema_version")
        if schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise PromotionError(
                f"the champion snapshot at {path} is schema {schema_version!r}; this build "
                f"reads schema {SNAPSHOT_SCHEMA_VERSION}"
            )
        try:
            return cls(
                tenant_id=str(value["tenant_id"]),
                captured_at=str(value["captured_at"]),
                actor=str(value["actor"]),
                source=str(value.get("source", "")),
                previous=_champion_from_dict(value["previous"]),
                promoted_to=_require_champion(_champion_from_dict(value["promoted_to"])),
            )
        except (KeyError, ValueError) as exc:
            raise PromotionError(f"the champion snapshot at {path} is malformed: {exc}") from exc


def _champion_to_dict(champion: TenantChampion | None) -> dict[str, str | None]:
    values: dict[str, str | None] = dict.fromkeys(CHAMPION_FIELDS)
    if champion is not None:
        for field in CHAMPION_FIELDS:
            values[field] = str(getattr(champion, field))
    return values


def _champion_from_dict(value: Any) -> TenantChampion | None:
    """Read a champion, or ``None`` for the all-null "no learned serving" state.

    A partially populated object is refused rather than repaired: the same
    all-three-or-none rule the check constraint enforces in the database has to
    hold for the file that can put values back into it.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("a champion must be an object or null")
    present = {field: value.get(field) for field in CHAMPION_FIELDS}
    named = [field for field, entry in present.items() if entry]
    if not named:
        return None
    if len(named) != len(CHAMPION_FIELDS):
        raise ValueError(f"a champion is all of {list(CHAMPION_FIELDS)} or none; got {named}")
    return TenantChampion(**{field: str(present[field]) for field in CHAMPION_FIELDS})


def _require_champion(champion: TenantChampion | None) -> TenantChampion:
    if champion is None:
        raise ValueError("expected a fully named champion")
    return champion


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def champion_from_bundle(
    bundle_dir: Path, manifest_name: str
) -> tuple[TenantChampion, str, dict[str, Any]]:
    """Verify a serving bundle, and return the coordinates it publishes.

    Everything about this call is the verification; the return value is a
    by-product. ``ServingManifest.load`` re-hashes every artifact the manifest
    declares and re-runs every structural validator, and it raises rather than
    reporting, so a bundle that has been edited, truncated or half-copied never
    reaches the write below.
    """
    # LightGBM lives behind this import (`src.models.artifacts` reads a booster's
    # recorded feature contract), and the slim API image ships none. Importing
    # inside the call is what keeps this module importable in every image that
    # imports `src.release`, exactly as `src.release.bootstrap` does for Feast
    # and httpx.
    try:
        from src.models.artifacts import ServingManifest
    except ImportError as exc:  # pragma: no cover - depends on the image, not the code
        raise PromotionError(
            "this environment cannot verify a serving bundle: reading a manifest needs "
            f"LightGBM, and importing it failed ({exc}). Run `make promote` from a checkout "
            "with the training dependencies installed, not from the slim API image."
        ) from exc

    manifest_path = bundle_dir / manifest_name
    if not manifest_path.is_file():
        raise PromotionError(
            f"no serving manifest at {manifest_path}; --bundle takes the directory that holds "
            f"{manifest_name} and the artifacts it pins"
        )
    try:
        manifest = ServingManifest.load(manifest_path)
    except (ValueError, OSError) as exc:
        raise PromotionError(
            f"the bundle at {bundle_dir} did not verify, so nothing was promoted: {exc}"
        ) from exc
    champion = TenantChampion(
        # The registry's `candidate_version` names the retrieval stage, whichever
        # family produced it — the same string `ChampionCoordinates.matches`
        # compares against `manifest.retriever.version` in the sidecar.
        candidate_version=manifest.retriever.version,
        ranker_version=manifest.ranker_version,
        feature_version=manifest.feature_version,
    )
    details = {
        "manifest": str(manifest_path),
        "schema_version": manifest.schema_version,
        "tenant_id": manifest.tenant_id,
        "retriever_family": manifest.retriever.family,
        "trained_at": manifest.trained_at,
        "lineage": manifest.lineage.to_dict(),
        "artifacts_verified": sorted(
            {ref.filename for ref in manifest.retriever.artifacts.values()}
            | {ref.artifact.filename for ref in manifest.rankers.values()}
        ),
    }
    return champion, manifest.tenant_id, details


def check_bundle_belongs_to_tenant(*, tenant_id: str, manifest_tenant_id: str) -> None:
    """Refuse to pin a tenant to a bundle published for a different one.

    Structural and not overridable. A manifest names the tenant it was built
    for, and promoting one tenant's bundle onto another is the cross-tenant bug
    class (non-negotiable #9) expressed as a configuration change — the request
    would then be served by a model fit on another tenant's interactions.
    """
    if manifest_tenant_id != tenant_id:
        raise PromotionError(
            f"this bundle was published for tenant {manifest_tenant_id!r} and --tenant is "
            f"{tenant_id!r}. A tenant is never served by another tenant's model; publish a "
            "bundle for this tenant instead."
        )


def check_run_is_authorized(settings: Settings, *, confirmed: bool) -> str:
    """What stands between an unintended run and a production tenant's champion.

    The threat is not a malicious operator — anyone who can reach the migrator
    DSN can write this row with ``psql``. It is an *accidental* run: a
    ``make promote`` typed in a shell that happens to have production
    credentials exported, a command copied out of the dev runbook, a job that
    inherited a production environment. All of those are stopped by requiring
    the run to say, in the command itself, that production is what it meant.

    Only production is gated. Dev and staging are where the mechanism gets
    exercised, and friction there would buy nothing: a rehearsal stack's
    champion is disposable, and the same ``Settings`` guards that let those
    environments hold dev credentials at all are conditioned on the same value.
    """
    if settings.environment != "production":
        return f"environment={settings.environment}, no confirmation required"
    if not confirmed:
        raise PromotionRefusedError(
            "ENVIRONMENT=production and --yes was not given, so nothing was written. A "
            "champion promotion against production is a deliberate act: re-run with --yes "
            "if this shell's credentials are the ones you meant to use. (`make promote` "
            "never passes it, so a stray invocation cannot repoint production.)"
        )
    return "environment=production, confirmed with --yes"


# --------------------------------------------------------------------------
# the write
# --------------------------------------------------------------------------


def read_tenant(connection: Connection, tenant_id: str) -> TenantRow:
    row = connection.execute(_TENANT_QUERY, {"tenant_id": tenant_id}).mappings().first()
    if row is None:
        raise PromotionError(
            f"no tenant {tenant_id!r} in public.tenants. A tenant is created by a migration, "
            "not by this tool — promotion moves an existing registry row and never invents one."
        )
    try:
        champion = _champion_from_dict(
            {field: row[f"champion_{field}"] for field in CHAMPION_FIELDS}
        )
    except ValueError as exc:
        # `ck_tenants_champion_complete` makes this unreachable through any
        # supported write path, so reaching it means the constraint is not on
        # this database. Refusing is the only safe answer: a half-named champion
        # is already serving nothing, and promoting over it would erase the
        # evidence of how it got that way.
        raise PromotionError(
            f"tenant {tenant_id!r} carries a partial champion in public.tenants ({exc}), which "
            "ck_tenants_champion_complete should make impossible. Repair the row and the "
            "constraint before promoting."
        ) from exc
    return TenantRow(
        tenant_id=str(row["id"]),
        champion=champion,
        updated_at=row["updated_at"],
    )


def apply_champion(
    connection: Connection,
    *,
    before: TenantRow,
    champion: TenantChampion | None,
) -> TenantRow:
    """Write the three columns, then prove the write did what it claimed.

    The re-read is inside the same transaction, so a disagreement rolls back
    with the caller's exception rather than leaving a row nobody checked.
    """
    parameters: dict[str, Any] = {"tenant_id": before.tenant_id}
    parameters.update(_champion_to_dict(champion))
    result = connection.execute(_PROMOTE_STATEMENT, parameters)
    if result.rowcount != 1:
        raise PromotionError(
            f"the promotion statement matched {result.rowcount} rows for tenant "
            f"{before.tenant_id!r}, expected exactly 1"
        )

    after = read_tenant(connection, before.tenant_id)
    if after.champion != champion:
        raise PromotionError(
            f"the row now reads {after.describe()} but this promotion wrote "
            f"{champion.describe() if champion else 'none'}; nothing was committed"
        )
    # Migration 0016 maintains `updated_at` from a BEFORE UPDATE trigger,
    # specifically because "when did this tenant's champion last move" is the
    # first question a bad promotion raises. Setting it here as well would be
    # duplicating the trigger's job and would hide its absence; asking whether it
    # moved catches the one case that matters — a schema restored or migrated
    # without the trigger, where every future promotion would be untimestamped.
    if after.updated_at == before.updated_at:
        raise PromotionError(
            "public.tenants.updated_at did not move, so migration 0016's "
            "tenants_before_write trigger is not on this database and no promotion here "
            "would be timestamped. Nothing was committed; run `alembic upgrade head`."
        )
    return after


# --------------------------------------------------------------------------
# the two operations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionRequest:
    """One resolved promotion: which tenant, to what, from where, on whose say-so."""

    tenant_id: str
    champion: TenantChampion | None
    source: str
    actor: str
    snapshot_path: Path
    write_snapshot: bool
    details: dict[str, Any]


def run_promotion(
    engine: Engine,
    request: PromotionRequest,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Read, report, write, report — or report and stop when nothing has to move."""
    with engine.begin() as connection:
        before = read_tenant(connection, request.tenant_id)
        logger.info(
            "champion_before tenant_id=%s champion=%s updated_at=%s",
            before.tenant_id,
            before.describe(),
            before.updated_at,
        )
        target = request.champion
        summary: dict[str, Any] = {
            "step": "promote",
            "tenant_id": request.tenant_id,
            "actor": request.actor,
            "source": request.source,
            "before": _champion_to_dict(before.champion),
            "after": _champion_to_dict(target),
            "before_updated_at": before.updated_at,
            "details": request.details,
        }

        if before.champion == target:
            # Idempotent, and silent about it in the database: re-running must not
            # burn an `updated_at`, and must not overwrite the snapshot — that
            # file is the revert target for the promotion that *did* move this
            # row, and rewriting it here would quietly discard it.
            logger.info(
                "champion_unchanged tenant_id=%s champion=%s", before.tenant_id, before.describe()
            )
            summary.update(
                {
                    "changed": False,
                    "snapshot": None,
                    "after_updated_at": before.updated_at,
                    "reason": "the tenant already stands on these coordinates; nothing written",
                }
            )
            return summary

        if dry_run:
            summary.update(
                {
                    "changed": False,
                    "snapshot": None,
                    "after_updated_at": before.updated_at,
                    "reason": "--dry-run: the bundle verified and this is what would move",
                }
            )
            logger.info(
                "champion_dry_run tenant_id=%s from=%s to=%s",
                before.tenant_id,
                before.describe(),
                target.describe() if target else "none",
            )
            return summary

        snapshot_path: str | None = None
        if request.write_snapshot:
            snapshot = Snapshot(
                tenant_id=request.tenant_id,
                captured_at=datetime.now(UTC).isoformat(),
                actor=request.actor,
                source=request.source,
                previous=before.champion,
                promoted_to=_require_champion(target),
            )
            # Written before the row changes, and deliberately before the commit.
            # A snapshot left behind by a transaction that then failed describes
            # the state the database is still in, so reverting from it is a no-op
            # — the harmless direction. The other order can lose the only record
            # of what a committed promotion replaced.
            write_snapshot(request.snapshot_path, snapshot)
            snapshot_path = str(request.snapshot_path)

        after = apply_champion(connection, before=before, champion=target)

    logger.info(
        "champion_promoted tenant_id=%s actor=%s source=%s from=%s to=%s updated_at=%s",
        after.tenant_id,
        request.actor,
        request.source,
        before.describe(),
        after.describe(),
        after.updated_at,
    )
    summary.update(
        {
            "changed": True,
            "snapshot": snapshot_path,
            "after_updated_at": after.updated_at,
            "reason": "promoted",
            # Said in the summary because it is the difference between a
            # promotion that looks broken for thirty seconds and one that is.
            "propagation": (
                "each API process caches public.tenants for 30s (TenantRouter's TTL), so "
                "requests may be answered by the previous champion until that window passes"
            ),
        }
    )
    return summary


def write_snapshot(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def snapshot_path_for(tenant_id: str, snapshot_dir: Path) -> Path:
    """Where this tenant's revert target lives.

    Derived from the tenant id rather than from a flag with a default, so the
    operator reverting at 3am looks in one place and finds it, and so two
    tenants cannot share a file.
    """
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_" for character in tenant_id
    )
    if not safe:
        raise PromotionError(f"tenant id {tenant_id!r} has no filename-safe form")
    return snapshot_dir / f"{safe}.json"


def build_promotion(
    settings: Settings,
    *,
    tenant_id: str,
    bundle_dir: Path,
    snapshot_dir: Path,
    actor: str,
) -> PromotionRequest:
    champion, manifest_tenant_id, details = champion_from_bundle(
        bundle_dir, settings.model_manifest_name
    )
    check_bundle_belongs_to_tenant(tenant_id=tenant_id, manifest_tenant_id=manifest_tenant_id)
    return PromotionRequest(
        tenant_id=tenant_id,
        champion=champion,
        source=str(bundle_dir),
        actor=actor,
        snapshot_path=snapshot_path_for(tenant_id, snapshot_dir),
        write_snapshot=True,
        details=details,
    )


def build_revert(
    *,
    tenant_id: str,
    snapshot_dir: Path,
    actor: str,
) -> PromotionRequest:
    """Restore the coordinates the last promotion replaced.

    No bundle is verified and none is required. The coordinates being restored
    were verified when they were promoted, the sidecar re-verifies the SHA-256s
    of whatever it loads on every boot, and demanding that the previous bundle
    still be on disk would make the emergency path depend on the state the
    emergency is about.
    """
    path = snapshot_path_for(tenant_id, snapshot_dir)
    if not path.is_file():
        raise PromotionError(
            f"no champion snapshot for tenant {tenant_id!r} at {path}, so there is nothing to "
            "revert to. A snapshot is written by a promotion that moved the row; if this "
            "tenant's champion was last set by a migration, that migration is the record."
        )
    snapshot = Snapshot.load(path)
    if snapshot.tenant_id != tenant_id:
        raise PromotionError(
            f"the snapshot at {path} belongs to tenant {snapshot.tenant_id!r}, not {tenant_id!r}"
        )
    return PromotionRequest(
        tenant_id=tenant_id,
        champion=snapshot.previous,
        source=f"{path} (captured {snapshot.captured_at} by {snapshot.actor})",
        actor=actor,
        snapshot_path=path,
        # The snapshot is left exactly as it is. Reverting twice then reads the
        # same file and finds the row already correct, which is the harmless
        # answer; rewriting it would make the second revert undo the first.
        write_snapshot=False,
        details={"snapshot": snapshot.to_dict()},
    )


def current_actor() -> str:
    """Who ran this, for the provenance line and the snapshot."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):  # pragma: no cover - a container with no passwd entry
        user = "unknown"
    return f"{user}@{socket.gethostname()}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.release.promote",
        description=(
            "Move one tenant's registered champion in public.tenants onto a verified serving "
            "bundle. Manual: this is not Phase 6's automatic routing gate."
        ),
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help="The tenant whose champion moves. Must already exist in public.tenants.",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help=(
            "Directory holding the serving manifest and the artifacts it pins. Every checksum "
            "is verified before anything is written."
        ),
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help=(
            "Restore the coordinates recorded in this tenant's snapshot instead of promoting. "
            "Takes no --bundle."
        ),
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help="Where the per-tenant revert snapshots live (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify, report what would move, and write nothing.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to write when ENVIRONMENT=production. Ignored elsewhere.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.revert and args.bundle is not None:
        parser.error("--revert restores the recorded coordinates and takes no --bundle")
    if not args.revert and args.bundle is None:
        parser.error("--bundle is required (or --revert to restore the previous coordinates)")

    try:
        settings = Settings()
    except Exception as exc:  # noqa: BLE001 - pydantic and the guards both land here
        print(
            f"[promote] Settings() refused to construct: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        summary = _dispatch(args, settings)
    except ReleaseError as exc:
        print(f"[promote] {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


def _dispatch(args: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    actor = current_actor()
    if args.revert:
        request = build_revert(tenant_id=args.tenant, snapshot_dir=args.snapshot_dir, actor=actor)
    else:
        request = build_promotion(
            settings,
            tenant_id=args.tenant,
            bundle_dir=args.bundle,
            snapshot_dir=args.snapshot_dir,
            actor=actor,
        )
    # After verification, before the connection: a bundle that does not verify
    # should say so whatever environment it was pointed at, and a refusal should
    # not depend on a database being reachable.
    authorization = (
        "--dry-run writes nothing, so no confirmation is required"
        if args.dry_run
        else check_run_is_authorized(settings, confirmed=args.yes)
    )

    engine = create_engine(settings.database_url, future=True)
    try:
        summary = run_promotion(engine, request, dry_run=args.dry_run)
    finally:
        engine.dispose()
    summary["authorization"] = authorization
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
