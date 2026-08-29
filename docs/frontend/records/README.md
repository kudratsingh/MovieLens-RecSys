# Frontend records

Documents in this directory were accurate on the date each one names, and are
kept because the reasoning in them is worth keeping. **They are not maintained.**
Each one's banner says what supersedes it.

All three predate the delivery of the movie-discovery product. For what the
frontend is today, start at [`../README.md`](../README.md).

| Record | Accurate as of | Superseded by |
|---|---|---|
| [Product discovery](product-discovery.md) | 2026-08-21 | [`design-contracts.md`](../design-contracts.md), [`implementation-plan.md`](../implementation-plan.md) |
| [Bundles 5–7 handoff](bundles-5-7-handoff.md) | 2026-08-21 | [`implementation-plan.md`](../implementation-plan.md), [`finish-gate-review.md`](../finish-gate-review.md) |
| [Baseline evidence](baseline-evidence.md) | 2026-08-21 (`c73b967`) | [`evidence/README.md`](../evidence/README.md) |
| [Finish-gate passes](finish-gate-passes.md) | 2026-08-21 → 2026-08-28 (four passes) | [`finish-gate-review.md`](../finish-gate-review.md) for the verdict |

**Finish-gate passes** is a different kind of record from the other three. It is
not superseded — it is the evidence the live verdict rests on, split out because
four appended passes had grown to 1,175 lines and the current verdict was buried
inside them. [`../finish-gate-review.md`](../finish-gate-review.md) carries the
verdict, the criteria table, the open findings and how to re-run the gate; this
file holds each pass verbatim, as written, because a later pass is appended
rather than folded into an earlier one and that sequence is the part worth
keeping.

Two of the other three are still cited by live documents rather than merely
archived, which is the reason they are kept rather than deleted:

- The **Bundles 5–7 handoff** defines the ten-step journey and the acceptance
  checklist that [`finish-gate-review.md`](../finish-gate-review.md) runs the gate
  against, and the review cites it by section.
- **Product discovery** holds the seven discovery tasks that the finish gate's
  outstanding moderated sessions are to be run over.

**Baseline evidence** is the pre-redesign screenshot matrix, captured on purpose
so the finish gate had a before to compare against. It is not a picture of the
current product; [`../evidence/README.md`](../evidence/README.md) indexes the sets
that are.
