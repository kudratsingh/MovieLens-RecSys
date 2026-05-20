import math


def recall_at_k(relevant: set[int], retrieved: list[int], k: int) -> float:
    """Fraction of relevant items that appear in the top-k retrieved list."""
    if not relevant:
        return 0.0
    hits = sum(1 for item in retrieved[:k] if item in relevant)
    return hits / len(relevant)


def ndcg_at_k(relevant: set[int], retrieved: list[int], k: int) -> float:
    """
    Normalized Discounted Cumulative Gain at k.

    DCG rewards relevant items that appear early in the ranking.
    Normalizing by the ideal DCG (all relevant items ranked first)
    puts scores on a [0, 1] scale regardless of how many relevant items exist.
    """
    dcg = _dcg(relevant, retrieved, k)
    idcg = _dcg(relevant, list(relevant)[:k], k)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def _dcg(relevant: set[int], retrieved: list[int], k: int) -> float:
    score = 0.0
    for rank, item in enumerate(retrieved[:k], start=1):
        if item in relevant:
            score += 1.0 / math.log2(rank + 1)
    return score
