# Orchestration protocol — MovieLens-RecSys

**Owner:** Kudrat. **Orchestrator:** the Claude session named `set-fable-model` (Fable 5.1).
This directory is local-only (excluded via `.git/info/exclude`, never committed). It is the
single channel between the owner's orchestrator and the working sessions. Absolute path:

    /Users/kudratsingh/Machine Learning Projects/MovieRecSys-MachineLearningProject/.coordination/

## Sessions

| Name | Runtime | Lane | Worktree(s) |
|---|---|---|---|
| Megatron | Codex (back online 2026-09-05 evening; failover agents D and E still finishing in this lane) | **Training and ranking.** `src/training/`, `src/models/candidates/sasrec*`, `src/models/ranker/`, `src/feature_contract.py`, the SASRec verdict docs, `docs/experiments/sasrec/` | `/private/tmp/MovieRecSys-sasrec-verdict` (+ new ones it creates) |
| Starscream | Claude Code | **Serving, evaluation apparatus, housekeeping.** `src/serving/`, `infra/`, manifest v2, generic retriever interface, k6 gate, `docs/status/`, CLAUDE.md status, worktree cleanup | `/private/tmp/MovieRecSys-noise` (+ new ones it creates) |
| Orchestrator | Claude Code | Reads everything, writes only this directory and its own PRs. Never edits another session's worktree. | main checkout on `feat/sasrec` (stale; read-only use) |

Lanes are exclusive. If a task needs a file in another lane, write the request in your report
and stop; the orchestrator re-assigns.

## Protocol

1. **Start of every turn:** `git fetch --all --prune`, then read `inbox/<your-name>.md` top to
   bottom. Instructions are dated and numbered; the newest block is at the top. An instruction
   marked `SUPERSEDED` is void.
2. **Do the work** in your lane only. One PR per work item from `docs/model-planning/work-items.md`
   unless the inbox says otherwise. Every model number goes through `src/evaluation/` and is
   recorded in `docs/results.md` with run id, protocol hash, machine, wall-clock.
3. **End of every turn, or after every run:** append a dated block to `reports/<your-name>.md`
   (newest at top): what landed (PR numbers, run ids, checksums, metrics), what is running
   (process, worktree, expected finish), what is blocked and on which decision, and anything
   you found that the plan did not anticipate. Keep it under 30 lines.
4. **Owner decisions** go to `DECISIONS.md` as a numbered question with a recommended answer.
   Do not answer them yourself; do not block on them if other lane work exists.
5. **Never**: open a PR outside your lane; rerun a full-data job that another session has
   already recorded; relax a gate threshold; touch another session's worktree; delete runs,
   artifacts, or logs.

## Standing rules (from the owner, 2026-09-05)

- One run per configuration. No seed-confirmation reruns until advanced transformer models.
- Item-item and LightGBM remain champions until a gate passes end to end.
- Each session's turn should produce a model number, unblock one, or land serving for one.
  Apparatus work is batched behind those.
- Pilots at 6% are for correctness only. Decisions are taken at full scale.
- SLOs do not move: encoder p99 < 15 ms, authenticated service p99 < 100 ms.

## Failover (owner instruction, 2026-09-05)

If Megatron stops responding or its work stalls (no report and no training process for a full
step), the orchestrator takes over the training lane with one or more Opus subagents, working in
their own worktrees from `origin/main` plus Megatron's pushed branches. Never edit Megatron's
worktree; pick up from what it pushed. Same lane rules and reporting apply to the subagents.

## Authorities granted by the owner, 2026-09-05

- **Merge on green CI** for lane PRs, without asking each time. Excluded: anything that changes a
  gate threshold, a non-negotiable in CLAUDE.md, or the tenant champion outside the rule below.
- **Champion swap**: the orchestrator may promote the per-route SASRec bundle (O-7) to served
  champion once M2's gates pass — artifact equivalence, isolated encoder p99 < 15 ms, service p99
  < 100 ms under the unchanged k6 profile, tenant isolation and audit unchanged, item-item bundle
  kept as the rollback. Record the swap as a dated note on ADR 0016 and in the roadmap log.
- **O-1 decided: warm-primary** for learned-route changes (see DECISIONS.md).
- **O-3 skipped** until the next sequence-model training proposal.

## Liveness (owner instruction, 2026-09-05)

- Every session and subagent appends a one-line heartbeat to its report file at least every 30
  minutes while a task is active (`- HB <time> <task> <what is happening>`), and a dated block when
  a task completes or fails. Silence is treated as a stop.
- The orchestrator runs a staleness monitor: 45 minutes without a report change on a lane with an
  assigned item triggers a ping; no answer within 15 minutes triggers reassignment or failover.
- Codex sessions act only on a user turn: one missed budget = hand the item to a failover agent.

## Codex sessions (Megatron) — liveness and handoff, 2026-09-05

A Codex session acts only when the owner gives it a turn and it stops silently when its usage runs
out, so the heartbeat rule is applied at turn boundaries instead of on a clock:

1. **Turn start:** first action is to append `- HB <time> <task> START (turn)` to `reports/megatron.md`
   after `git fetch --all` and reading the inbox.
2. **During a turn:** for anything longer than 20 minutes (a full-data run, a long test suite), append an
   HB line at each phase boundary (data loaded / epoch 1 / evaluation / gate / PR opened).
3. **Turn end — mandatory, before the final message to the owner:** update `HANDOFF-megatron.md` in this
   directory with: the item in progress, branch and worktree, last commit pushed, exactly what is
   done and what is not, the next command to run, and any process still running with its log path.
   Then append `- HB <time> <task> END (turn): <one line>` to the report.
4. **If a usage limit hits mid-turn**, the session cannot write anything, so the HANDOFF file from the
   previous turn end is what the failover agent resumes from. Keep it accurate every turn.
5. **Orchestrator side:** 45 minutes without an HB on an active Codex item = the owner is asked once to
   give it a turn; if the owner is away or the session is out of usage, the item is handed to a
   failover agent, which reads `HANDOFF-megatron.md` first and continues from the last pushed commit.
6. **Owner's paste to restart Megatron is always the same:** "Read `.coordination/inbox/megatron.md`
   and `.coordination/HANDOFF-megatron.md`, then continue."

The same HANDOFF discipline applies to Claude sessions and subagents (`HANDOFF-<name>.md`), but they
also run the 30-minute clock heartbeat because they can.

## Work queues (owner instruction, 2026-09-05 21:30)

Each inbox carries a `## QUEUE` list kept at least two items ahead by the orchestrator. A session that
finishes a task re-reads its inbox and starts the next unchecked item in the same turn; it ends its
turn only when the queue is empty or the next item says "owner decision required". The owner never has
to hand work to a session; a paste is needed only to give a Codex session a turn after a usage limit.

## Process checks (lesson, 2026-09-05 evening)

`pgrep -f` / `ps | grep <pattern>` are unreliable here: they match a monitor's or watcher's own shell
argv that merely *contains* the pattern. Two false alarms came from this. Check liveness by executable
name, not argv — `ps -Ao pid,rss,comm | awk '$3 ~ /python/'`, `ps -Ao pid,comm | awk '$2 ~ /k6$/'` —
and prefer the artifact a process would produce (a log, `gate-started-at.txt`, a snapshot dir) as the
authoritative signal. Trigger tokens between lanes are literal, start-of-line, and never quoted in prose.

## Wind-down, 2026-09-05 23:45 (owner instruction)

Finish only what is in flight: the two-tower full run (F), the TMDB pull → load → coverage
(orchestrator), PR #178 merge, G's ADR 0020 PR and Q1 draft. No new items start. When those land,
every session stops its watches and heartbeats, updates its HANDOFF, and stops. Queues resume on the
owner's word; the QUEUE lists in the inboxes remain the starting point.

## Liveness addendum (lesson, 2026-09-06 00:00)
Local commits are invisible to the orchestrator. A session that works without pushing and without
heartbeats looks identical to one that has stopped, and its items will be reassigned. Push a WIP
branch (`git push -u origin <branch>`) at the first commit and heartbeat; the staleness monitor now
also counts a new remote branch in the lane as activity.

## Mode change, 2026-09-06 morning (owner)
Single worker: Megatron (Codex) owns all lanes; no Claude session or failover agent works unless the
owner says so. The orchestrator manages via inbox/megatron.md, merges on green, and conserves its own usage.

## Worktree location (lesson, 2026-09-10)
macOS purges `/private/tmp` entries untouched for ~3 days: every `/private/tmp/MovieRecSys-*` worktree lost
its `.git` link over the 6–10 Sept break and git pruned the registrations. All branches were on origin
except `feat/sasrec-all-positions`, which the orchestrator pushed on 2026-09-10. From now on, create
worktrees under `~/worktrees/MovieRecSys-<name>` (never under /tmp), and push at the first commit.
