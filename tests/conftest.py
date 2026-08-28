"""Process-wide settings every test run needs before the first model import.

LightGBM links Homebrew's libomp on macOS while scikit-learn, FAISS, and torch
each ship their own copy. The first OpenMP parallel region in a process that
has loaded two of them dies with a segmentation fault inside the runtime's
worker threads, and the load of the committed ranker (`Booster.__init__`) is
usually where that lands. One thread sidesteps the conflict entirely, is what
the serving images already pin, and makes LightGBM's histogram construction
deterministic — which the artifact-hash tests need anyway. Set before any
test module imports a model library; a shell that already chose a value wins.
"""

import os

for _variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")
