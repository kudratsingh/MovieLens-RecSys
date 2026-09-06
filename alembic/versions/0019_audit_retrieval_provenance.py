"""record which retriever answered, from which artifact, on recommendation audits

Revision ID: 0019_audit_retrieval_provenance
Revises: 0018_tmdb_catalog

A ``recommendation_audits`` row already says which policy ran, which versions
were loaded and what each stage cost.  It does not say which *retrieval family*
answered, which artifact it answered from, which of the bundle's two boosters
scored the result, or how much of the request was the sequence encoder.  With a
SASRec bundle now loadable in the sidecar and a champion swap pending, that gap
is immediate: an audit row cannot distinguish an item-item answer from a SASRec
one, so a promotion cannot be verified from the audit log and a regression
cannot be attributed to the model that caused it.

Four columns close it.

``retriever_family`` is the manifest's family name for the retriever that
actually answered.  It is not derivable from the ``policy`` column: that string
reads ``popularity`` for every fallback and ``popularity-fill+lightgbm`` for a
retrieval no seed reached, and both lose the family entirely.

``retriever_sha256`` is the SHA-256 of the retriever's primary artifact, taken
from the serving manifest.  This is the column that makes a row *replayable*.
``candidate_version`` names a bundle, and a version string can be republished
over different weights; a checksum cannot.  Shape-checked on the way in
(64 lowercase hex characters, or empty when nothing was pinned) and never
recomputed by the API, which holds no artifacts to hash.

``ranker_route`` says which of manifest v2's two boosters scored the
candidates.  Both routes ship in one bundle under one ``ranker_version`` for a
schema 1 document, so the version alone cannot answer "did this request get the
learned ranker".  The popularity fallback records ``fallback`` rather than a
third value, because a reader asking which requests missed the learned booster
has to find those requests.

``encoder_ms`` is time inside the sequence encoder's forward pass, isolated
from the ANN search and the exclusion filtering around it.  ``0.0`` is a
measurement for item-item, not a gap: it runs no encoder and spends none.

**All four are nullable with no server default, and that is the point.**
NOT NULL with a default would backfill every existing row with a claim about a
request nobody measured — that its retriever was unpinned, that its encoder cost
nothing, that its route was whatever the default said.  Those rows predate the
columns; the honest value is NULL, which reads as "this row is older than the
question".  The writer never stores NULL — it always supplies a value, using
``not-run``/``""``/``0.0`` when a request genuinely ran no model — so NULL and
"ran nothing" stay distinguishable in a query, which is the whole reason to
record any of this.  ``ck_audit_encoder_ms`` still holds: a CHECK is satisfied by
NULL, so the constraint binds every row that carries a number.

``recommendation_audits`` keeps its forced RLS policy and the least-privilege
grants from 0008.  Table-level grants already cover added columns; they are
re-issued here for the reason 0012 re-issued them — so the intended privilege
surface stays visible beside every column addition and cannot quietly widen.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_audit_retrieval_provenance"
down_revision: str | None = "0018_tmdb_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN_NAMES = (
    "retriever_family",
    "retriever_sha256",
    "ranker_route",
    "encoder_ms",
)

_CHECKS = (("ck_audit_encoder_ms", "encoder_ms >= 0"),)


def _columns() -> list[sa.Column[object]]:
    # Built fresh on each call: a Column instance binds to the table it is added
    # to, so the same object cannot be reused across operations.
    return [
        sa.Column("retriever_family", sa.Text, nullable=True),
        sa.Column("retriever_sha256", sa.Text, nullable=True),
        sa.Column("ranker_route", sa.Text, nullable=True),
        sa.Column("encoder_ms", sa.Float, nullable=True),
    ]


def upgrade() -> None:
    for column in _columns():
        op.add_column("recommendation_audits", column)
    for name, expression in _CHECKS:
        op.create_check_constraint(name, "recommendation_audits", expression)
    op.execute("GRANT SELECT, INSERT ON recommendation_audits TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON recommendation_audits TO admin_user;")


def downgrade() -> None:
    for name, _ in reversed(_CHECKS):
        op.drop_constraint(name, "recommendation_audits", type_="check")
    for column_name in reversed(_COLUMN_NAMES):
        op.drop_column("recommendation_audits", column_name)
