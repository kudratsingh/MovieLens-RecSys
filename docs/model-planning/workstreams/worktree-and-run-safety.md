# Worktree and run-isolation workstream

## Purpose

Protect concurrent development sessions and make every experiment attributable. Planning, training,
artifact generation, and documentation should remain inside the worktree and run roots explicitly
created for that effort.

## Git isolation contract

For each implementation effort:

1. Start from a recorded base commit.
2. Create a uniquely named branch and linked worktree.
3. Verify branch, worktree path, base SHA, and clean/known status before editing.
4. Read repository instructions from the base and worktree before delegating.
5. Limit edits, formatting, tests, staging, and commits to the assigned worktree.
6. Inspect staged paths and branch again immediately before commit.
7. Never merge, rebase, cherry-pick, push, delete worktrees, or modify another session's branch unless
   the owner explicitly expands the task.

Untracked or modified files in another worktree belong to that session. Do not clean, stash, move,
format, stage, or inspect their contents beyond the minimum read-only status needed to avoid overlap.

## Experiment run roots

Every run receives an explicit ID and writes beneath a run-scoped root, for example:

`artifacts/runs/<run-id>/`

The run root contains or references:

- immutable resolved config and protocol manifest;
- code SHA, branch, worktree path, and dirty flag;
- stdout/stderr and structured event log;
- checkpoints, metrics, profiles, and evaluation outputs;
- artifact checksums and final run state;
- parent/sweep identity where applicable.

Production-scale artifacts may live in object storage, but local paths remain namespaced references
or caches. Never write a generic `model.pkl`, `results.json`, or `latest/` shared by concurrent runs.

## Workspace preflight

Before a run or batch:

- assert the current directory resolves inside the assigned worktree;
- assert the active branch is the expected branch;
- record `HEAD` and whether tracked/untracked changes exist;
- validate all output, cache, checkpoint, and temporary roots are run-scoped;
- resolve the DVC/data revision and environment identity;
- reject output paths that resolve to a repository root, home directory, or another linked worktree;
- check disk space and expected artifact footprint;
- acquire a run-scoped lock where duplicate launch is possible.

A dirty worktree can be allowed for exploratory work only when captured in provenance. Promotion
evidence must come from committed code or an archived diff with an explicit exception.

## Parallel-agent boundaries

- Delegate concrete, non-overlapping scopes with named output paths.
- Agents may read shared committed context but edit only their assigned worktree/subpaths.
- One integrator owns overlapping index/navigation files and final staging.
- Agents report findings instead of modifying unrelated issues they discover.
- Before integration, inspect changed paths and reconcile concurrent edits deliberately.

If agents share one physical worktree, assign disjoint files and avoid concurrent formatters or git
operations. Prefer independent worktrees for implementation agents when commits are expected.

## Run-state machine

Use explicit terminal states:

- `planned`, `running`, `succeeded`, `failed`, `cancelled`, `invalid`;
- `invalid` covers leakage, protocol mismatch, corrupt/missing artifacts, or provenance failure;
- only `succeeded` and valid runs may feed an aggregate;
- interruption records the last durable checkpoint and whether resumption is compatible.

Retries receive new attempt identities linked to the logical run. They do not overwrite evidence
from a failed attempt.

## Secrets, datasets, and caches

- Secrets come from the approved environment/secret manager and never enter configs, logs, or git.
- Large raw/derived data and checkpoints follow DVC/object-store policy rather than normal commits.
- Caches are namespaced by semantic inputs and safe to delete without losing source truth.
- Cache hits record key/version; partial writes use atomic finalize/rename semantics.
- Logs avoid raw user histories and redact credentials or signed URLs.

## Completion checklist

Before handing off an effort:

- expected branch/worktree and base are recorded;
- only intended files changed and `git diff --check` passes;
- relevant tests/link/manifest validators pass;
- run/artifact paths are scoped and no large generated object is accidentally staged;
- decisions, assumptions, failures, and follow-ups are documented;
- original and other known worktrees show the same status/HEAD as at start;
- no push, merge, cleanup, or cross-worktree mutation occurred without authorization.

## Exit criteria

- Automated preflight refuses unsafe branch/output combinations.
- Concurrent smoke runs cannot overwrite each other's files.
- Every result points to a code/data/config/environment identity.
- Invalid or interrupted runs remain distinguishable from negative scientific results.
- The handoff process proves other sessions and worktrees were left untouched.
