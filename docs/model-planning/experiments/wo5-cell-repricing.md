# WO-5 preparation: cell A timed, ADR 0020's cell table re-priced

**Status:** measured. Preparation only: **no cell was started**. Nothing here is a model result.

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md): the cost model in "The finding that
governs the cost of everything below", stop rule 7, and D6 in the 2026-10-05 amendment. Also the
build brief's WO-5, "Before the first cell".

**Owner approval:** "WO-5: preparation only", 2026-10-05, relayed by the coordinator. The coordinator
approved the deviation below the same day.

**Specification:** [`wo5-prep-cellA-step-timing-full.json`](../../experiments/sasrec/wo5-prep-cellA-step-timing-full.json).
It was committed before the runs (`778953c`); its `result` block holds the measurements.

## What WO-5 asks, and the deviation

WO-5: "Time one pass of cell A and re-price the whole table from it … If any cell would take longer
than one night, bring the hardware options in the run budget to the owner."

**Deviation: 2,000 timed steps instead of one whole pass.**

*Why.* One CPU pass of cell A was projected at **23–24 hours** before anything ran:
- ADR 0020's per-example cost `A(L,d,b) = 2·b·L·d·(6d+L)` puts cell A at 8.0× cell 0b;
- cell 0b's step was measured at 0.297 s, so cell A would be about 2.2 s per step;
- a full pass is 38,554 steps.

ADR 0020's own projection was 27.1 h. The timing pass alone would have held the machine's one
full-data slot for a day.

*What ran instead.*
- The first 2,000 optimizer steps of cell A on the full dataset: seed 42, one thread, early
  stopping off, on the CPU.
- Then the same 2,000 steps on `mps`, as a timing only. It is not a result of record.
- Steps 1–20 are excluded as warm-up.
- The pass time is the steady-state mean step time × 38,554 steps.

*Why this extrapolates.* Every step has the same shape: 512 examples of 100-long left-padded
windows, 1,024 negatives, and a fixed 34,461-movie catalog. Padding does not change the arithmetic,
because attention over a padded row costs the same as over a full one. The error should be a few
percent. The measurement agrees: the second half of the steps ran 1.5% faster than the first on the
CPU and 0.2% on `mps`.

*The literal option.* If the owner wants the measured whole pass, it is the same command with the
step limit removed: about 15 h on the CPU, or about 6 h on `mps`.

## Measurements

Both runs used the full 25M split (cutoff 1466837397), the objective of record, and torch 2.13.0
with `OMP_NUM_THREADS=1`. Each ran on a machine otherwise idle of training (`pgrep -fl
src.training` was empty before each). The CPU run was the one full-data run in flight.

| | CPU | `mps` (timing only) |
|---|---:|---:|
| Window (UTC) | 17:16:32–18:04:12 | 18:04:26–18:23:30 |
| Data load (CSV) / split | 3.5 s / 0.7 s | 2.7 s / 0.6 s |
| User-history build / example-store build | 12.8 s / 11.2 s | 12.4 s / 11.1 s |
| Fit setup before the first step (incl. the two builds) | 29.2 s | 29.6 s |
| Training examples / steps per pass (512 per step) | 19,739,546 / 38,554 | same |
| Examples with more than 100 earlier movies (truncated at L=100) | 10,753,559 (54.5%) | same |
| Steady step, mean (median; p10–p90) | **1.4097 s** (1.381; 1.336–1.530) | **0.5502 s** (0.549; 0.545–0.556) |
| First half against second half of the steady steps | 1.420 / 1.399 s | 0.551 / 0.550 s |
| 2,000 steps, wall | 2,847.9 s | 1,130.0 s |
| **Extrapolated pass** | **54,350 s = 15.10 h** | **21,213 s = 5.89 h** |
| Example store | 395.8 MB | 395.8 MB |
| Peak RSS (`ru_maxrss`) | 4,241,768,448 bytes | 4,844,748,800 bytes |
| Process CPU time | 2,716 s user | 77 s user |

- **Speed-up from `mps` at cell A's shape:** 2.56× per step. Cell 0b's pilots measured 2.72× per
  pass, so the Mac's GPU does not gain on the CPU as the model grows, at least from 0b to A.
- **Peak memory footprint** (macOS `time -l`, CPU run): 8.76 GB.
- **The partition:** the fitted frame's latest timestamp is 1466837396, below the sealed boundary
  1469256597. Nothing was scored.

## Re-pricing method

1. **Cell A is the anchor**, at its measured pass time on each device.
2. **Cell 0b is measured directly.** The pilots ran exactly its step shape (512 × 50 windows, 1,024
   negatives, same catalog). At 0.297 s on the CPU and 0.109 s on `mps`, a pass is 3.2 h and 1.2 h.
   A 6% pilot's step is a full-data step, only fewer of them.
3. **Every other cell is the anchor × ADR 0020's own cost ratio**, `A(L,d,b) / A(100,128,2)`.
   - Cells with the full softmax add the ADR's own output-layer increment: E − A = 9.6% of A at
     width 128, scaled with width.
   - In this implementation the sampled softmax already computes the full catalog's logits and
     gathers 1,025 columns, so E's true increment is smaller. This is conservative.
4. **The hand-written encoder's ~22% slowdown** (WO-2, against PyTorch's packaged encoder) is already
   inside both measured steps, so it is not applied again. ADR 0020's projections were made on the
   packaged encoder; they are shown as published and × 1.22 for comparison.
5. **Sensitivity: the ADR model overstates growth on this CPU.** It predicted cell A at 8.0× cell
   0b; the measurement is 4.75×. A wider model uses the BLAS kernels better.
   - Fitting cost ∝ FLOPs^α to that one pair gives α = 0.749.
   - The "sub-linear" column applies that exponent. For cells larger than A it is the likelier
     figure, and the ADR-model column is closer to an upper bound. This rests on one pair of shapes.
6. **`mps` columns use the same ratios.** The 0b-to-A comparison suggests `mps` does not scale
   better than the CPU here, but that too is one pair.
7. **Per-pass extras are excluded:** the probe and holdout scoring, and the final export and
   evaluation. They are minutes against hours.
8. **"One night" is taken as 12 hours.** O-3 gives no number.

## The table

Passes are 3 to 5 per cell, with early stopping (ADR 0020). Hours are per full-data pass unless
marked.

| Cell | Width / blocks / history | Loss | Cost vs A (ADR model) | ADR 0020 h/pass (packaged) | ×1.22 | **CPU h/pass** | CPU h, 3–5 passes | CPU sub-linear h/pass | **`mps` h/pass** | `mps` h, 3–5 passes | Over one night (12 h)? |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0b | 64 / 2 / 50 | sampled, 1,024 | 0.125 | 3.41 | 4.2 | **3.2** (measured) | 10–16 | 3.2 | **1.2** (measured) | 4–6 | CPU: only at 4–5 passes; `mps`: no |
| A | 128 / 2 / 100 | sampled, 1,024 | 1.000 | 27.1 | 33.1 | **15.1** (measured) | 45–75 | 15.1 | **5.9** (measured) | 18–29 | CPU: yes; `mps`: yes |
| B | 256 / 2 / 100 | sampled, 1,024 | 3.770 | 101.9 | 124.3 | **56.9** | 171–285 | 40.8 | **22.2** | 67–111 | CPU: yes; `mps`: yes |
| C | 128 / 4 / 100 | sampled, 1,024 | 2.000 | 54.1 | 66.0 | **30.2** | 91–151 | 25.4 | **11.8** | 35–59 | CPU: yes; `mps`: yes |
| D | 128 / 2 / 200 | sampled, 1,024 | 2.230 | 60.3 | 73.6 | **33.7** | 101–168 | 27.5 | **13.1** | 39–66 | CPU: yes; `mps`: yes |
| E | 128 / 2 / 100 | full softmax | 1.096 | 29.7 | 36.2 | **16.5** | 50–83 | 16.5 | **6.5** | 19–32 | CPU: yes; `mps`: yes |
| A′ (stop rule 3: 128/2/50) | 128 / 2 / 50 | sampled, 1,024 | 0.471 | — | — | **7.1** | 21–36 | 8.6 | **2.8** | 8–14 | CPU: yes; `mps`: only at 4–5 passes |
| B+C (combination) | 256 / 4 / 100 | sampled, 1,024 | 7.539 | — | — | **113.8** | 341–569 | 68.6 | **44.4** | 133–222 | CPU: yes; `mps`: yes |
| B+D (combination) | 256 / 2 / 200 | sampled, 1,024 | 8.000 | — | — | **120.8** | 362–604 | 71.7 | **47.1** | 141–236 | CPU: yes; `mps`: yes |
| B+E (combination) | 256 / 2 / 100 | full softmax | 3.961 | — | — | **59.8** | 179–299 | 43.7 | **23.3** | 70–117 | CPU: yes; `mps`: yes |
| C+D (combination) | 128 / 4 / 200 | sampled, 1,024 | 4.461 | — | — | **67.3** | 202–337 | 46.3 | **26.3** | 79–131 | CPU: yes; `mps`: yes |
| C+E (combination) | 128 / 4 / 100 | full softmax | 2.096 | — | — | **31.6** | 95–158 | 26.8 | **12.4** | 37–62 | CPU: yes; `mps`: yes |
| D+E (combination) | 128 / 2 / 200 | full softmax | 2.326 | — | — | **35.1** | 105–176 | 29.0 | **13.7** | 41–69 | CPU: yes; `mps`: yes |

The conditional rows: A′ is the one disambiguating cell stop rule 3 allows if A fails gate 1. The
combination rows are the six pairings the "one combined cell" rule could produce; at most one of
them would ever run.

**The six predeclared cells (0b, A–E) at 3–5 passes:**

| Device | Hours | Days |
|---|---:|---:|
| CPU, ADR model | 467–778 | 19.5–32.4 |
| CPU, sub-linear | 386–643 | 16.1–26.8 |
| `mps` | 182–303 | 7.6–12.6 |

ADR 0020 priced the same grid on this objective at 35–58 CPU-days with the packaged encoder.

## What crosses one night

- **On the CPU, every capacity cell crosses one night, at any pass count.** That is A, B, C, D, E,
  A′ and every combination. One pass of A alone is 15.1 h.
- **Cell 0b fits on the CPU only if early stopping ends it at 3 passes** (9.5 h). At 4–5 passes it
  is 12.7–15.9 h.
- **On `mps`, every capacity cell crosses one night across its 3–5 passes.** A runs 18–29 h, and
  even its single pass is 5.9 h. 0b fits (3.5–5.8 h); A′ fits only at 3 passes.

**Stop rule 7 does not fire.** It kills a cell whose pass runs more than 2× its projection.
Measured A is **0.56×** ADR 0020's 27.1 h projection, or 0.46× the 33.1 h hand-written equivalent:
faster than projected, not slower. The table is re-costed here, as the rule and WO-5 ask, before any
cell starts.

**This goes to the owner (WO-5; D6; O-3's trigger).** Every capacity cell costs more than one night
on the fixed CPU loop. The options are:

| Option | Cost |
|---|---|
| The laptop CPU | 16–32 days for the six cells, plus any conditional |
| The Mac's GPU | 7.6–12.6 days. It needs the D6 tolerance accepted first (proposed in [`wo4-trainer-upgrades.md`](wo4-trainer-upgrades.md): ±1.0% at full scale, after a calibration pair of cell 0b on both devices) |
| A rented GPU | ADR 0020 estimated $10–51 on demand for its grid. Prices are not re-checked here, and nothing is spent |

All of it assumes the objective of record, `strict-prefix-final-position-v1`. **WO-3's outcome
changes this table.** If its repaired objective passes, ADR 0020 projected the same grid at 17–29
hours, and the table should be re-timed on that objective before any cell runs.

## Commands

The timing script lived at `models/logs/time_cellA.py` in the WO-4 worktree (git-ignored). It:
- loads the CSVs and applies `temporal_split`;
- wraps `build_user_history` and `build_strict_prefix_example_store` to time them;
- calls `SASRecModel.fit` on `split.train` with an `on_step` callback that timestamps each step and
  raises after the 2,000th;
- writes the step statistics and `ru_maxrss`.

It uses `split.train` without the synthetic cold-start cohort. The cohort's users each have one
timestamp, so they contribute no training example and no step.

```bash
MAIN=/Users/kudratsingh/Machine-Learning-Projects/movielens-recsys
WT=$MAIN-wt-wo4
cd $WT && caffeinate -i /usr/bin/time -l env PYTHONPATH=$WT OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE \
  TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m $MAIN/.venv/bin/python models/logs/time_cellA.py \
  docs/experiments/sasrec/wo5-prep-cellA-step-timing-full.json wo5-prep-cellA-timing-cpu 2000 \
  models/logs/wo5-cellA-cpu-timing.json
# then the same with the label wo5-prep-cellA-timing-mps
```

<details>
<summary>The timing script, verbatim</summary>

```python
"""WO-5 preparation: time the first N optimizer steps of one cell on the full dataset, then stop.

Usage: python time_cellA.py <cells.json> <label> <steps> <out.json>
Times data loading, the split, the user-history build and the example-store build separately
from stepping; records every step's wall time and the process's peak RSS. No evaluation, no
export, no MLflow: the fit is interrupted after the last timed step.
"""
import json, os, resource, statistics, sys, time
from pathlib import Path

t_start = time.perf_counter()
import torch  # noqa: E402

import src.models.candidates.sasrec as sasrec  # noqa: E402
from src.config import Settings  # noqa: E402
from src.data.split import temporal_split  # noqa: E402
from src.training.candidate_data import load_inputs  # noqa: E402
from src.training.sasrec import sealed_partition_params  # noqa: E402
from src.training.sasrec_sweep import parse_grid  # noqa: E402

spec_path, wanted, n_steps, out_path = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
fraction, cells = parse_grid(json.loads(Path(spec_path).read_text()))
assert fraction == 1.0
label, config = next((l, c) for l, c in cells if l == wanted)
timings: dict[str, float] = {"imports_seconds": time.perf_counter() - t_start}

t = time.perf_counter()
ratings, _movies = load_inputs(Settings(), input_dir=Path(os.environ["TWOTOWER_INPUT_DIR"]))
timings["load_seconds"] = time.perf_counter() - t
t = time.perf_counter()
split = temporal_split(ratings)
partition = sealed_partition_params(ratings, split, split.train)
timings["split_seconds"] = time.perf_counter() - t

for name in ("build_user_history", "build_strict_prefix_example_store"):
    original = getattr(sasrec, name)
    def timed(*args, _original=original, _name=name, **kwargs):
        t0 = time.perf_counter()
        result = _original(*args, **kwargs)
        timings[f"{_name}_seconds"] = time.perf_counter() - t0
        return result
    setattr(sasrec, name, timed)

class Enough(Exception):
    pass

stamps: list[float] = []
losses: list[float] = []
def on_step(step):
    stamps.append(time.perf_counter())
    losses.append(step.loss)
    if len(stamps) >= n_steps:
        raise Enough

model = sasrec.SASRecModel(config=config)
t_fit = time.perf_counter()
try:
    model.fit(split.train, on_step=on_step, retrieval_backend="torch")
    raise SystemExit("fit finished before the step budget; nothing to extrapolate")
except Enough:
    pass
first_step_done = stamps[0]
step_seconds = [stamps[0] - t_fit] + [b - a for a, b in zip(stamps, stamps[1:])]
warm = 20
steady = step_seconds[warm:]
stats = model._training_stats
steps_per_pass = -(-stats.n_targets // config.batch_size)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
mean_steady = statistics.fmean(steady)
result = {
    "label": label,
    "device": config.device,
    "torch_version": torch.__version__,
    "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
    "config": config.as_params(),
    **timings,
    "fit_setup_seconds_before_first_step": (first_step_done - t_fit) - step_seconds[1],
    "steps_timed": len(step_seconds),
    "warmup_steps_excluded": warm,
    "first_step_seconds": step_seconds[0],
    "steady_mean_step_seconds": mean_steady,
    "steady_median_step_seconds": statistics.median(steady),
    "steady_p10_p90_step_seconds": [statistics.quantiles(steady, n=10)[0], statistics.quantiles(steady, n=10)[-1]],
    "first_half_vs_second_half_mean": [statistics.fmean(steady[: len(steady) // 2]), statistics.fmean(steady[len(steady) // 2 :])],
    "stepping_wall_seconds": stamps[-1] - t_fit,
    "n_training_examples": stats.n_targets,
    "n_truncated_examples": stats.n_truncated_sequences,
    "steps_per_pass": steps_per_pass,
    "extrapolated_pass_seconds": mean_steady * steps_per_pass,
    "extrapolated_pass_hours": mean_steady * steps_per_pass / 3600,
    "example_store_bytes": model._training_example_bytes,
    "peak_rss_bytes": peak if sys.platform == "darwin" else peak * 1024,
    "first_and_last_step_loss": [losses[0], losses[-1]],
    "partition": partition,
    "n_items": len(model._index_to_item),
}
Path(out_path).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k != "config"}, indent=1))
```

</details>
