# `gate` is deliberately not re-exported here. `python -m src.evaluation.gate` is the
# CLI Phase 4 and `make gate` call, and importing the module from this package
# `__init__` makes runpy warn that it was already in sys.modules before it ran.
# Import it directly: `from src.evaluation.gate import promotion_decision`.
from .aggregate import mean_eval_result
from .metrics import ndcg_at_k, recall_at_k
from .protocol import EvalResult, UserMetrics, evaluate

__all__ = [
    "evaluate",
    "mean_eval_result",
    "EvalResult",
    "UserMetrics",
    "recall_at_k",
    "ndcg_at_k",
]
