# Model scorecards

Scorecards are concise decision summaries. They link to `docs/results.md` and MLflow rather than
copying the full experiment history.

Create one file per material model generation using [`template.md`](template.md), for example:

- `two-tower-v2.md` — measured, not promoted;
- `sasrec-v1.md` — in progress, then final verdict;
- `sequence-aware-ranker-v1.md` — only after Rung 3 approval.

Allowed lifecycle states:

- proposed;
- research in progress;
- research complete — not promoted;
- promotion eligible;
- serving eligible;
- champion;
- superseded;
- archived.

Scorecards never substitute for gate output. A status change requires evidence links and, for a
champion change, explicit owner approval.
