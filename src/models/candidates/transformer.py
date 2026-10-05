"""A causal, pre-norm Transformer encoder written out by hand (WO-2, rule D7).

Every layer here is built from ``nn.Linear``, ``nn.Dropout``, ``nn.Parameter`` and
plain tensor arithmetic. Nothing calls into PyTorch's packaged attention or
normalization code, so every number this module produces can be traced to a line
in this file. ``docs/modeling/transformer-from-scratch.md`` walks through it layer
by layer, with the tensor shapes and both masks.

The arithmetic is deliberately a replica of what SASRec v1 ran on before this file
existed — PyTorch's built-in encoder layer with ``norm_first=True``, GELU, and no
final norm inside the stack — so that every saved v1 model converts one-to-one
(``sasrec_artifact.legacy_state_to_hand_written``) and scores the same:

* pre-norm residual blocks: ``x + attention(norm(x))`` then ``x + feed_forward(norm(x))``;
* attention scaled by ``1 / sqrt(head_dim)``, with dropout on the attention
  weights, on the attention output, inside the feed-forward after GELU, and on the
  feed-forward output — the same four places;
* layer normalization over the last dimension with the biased variance and
  ``eps = 1e-5``;
* the same parameter initialization, drawn from the random generator in the same
  order, so a freshly constructed encoder equals a freshly constructed v1 encoder
  bit for bit at the same seed (``tests/unit/test_sasrec_transformer.py`` pins it).

One behaviour is new and intentional: **a padded position always comes out as
exact zeros and can never produce NaN**, in either mode, at any depth. The old
encoder relied on a process-wide switch to keep PyTorch's inference fast path off,
because that path turned every left-padded history into NaN. Here the property is
local to the code: attention never divides by an empty row, and the stack resets
padded rows to zero after every block, so nothing downstream can depend on what a
padded row would otherwise have held.
"""

from __future__ import annotations

import copy
import math

import torch
import torch.nn.functional as F  # noqa: N812
from torch import nn

LAYER_NORM_EPS = 1e-5


def _linear(in_features: int, out_features: int) -> nn.Linear:
    """An ``nn.Linear`` whose weights are left for the caller to initialize.

    Constructing ``nn.Linear`` normally draws its weights from the global random
    generator straight away. Each block below initializes its own weights
    explicitly, in the order v1 drew them, and an extra draw at construction time
    would shift every later draw — so the layer is built on PyTorch's ``meta``
    device, where nothing is drawn, and then given real (uninitialized) storage.
    """
    return nn.Linear(in_features, out_features, device="meta").to_empty(device="cpu")


class LayerNormalization(nn.Module):
    """Normalize each position's vector to zero mean and unit variance, then rescale.

    ``x``: ``(..., d)``. The statistics are taken over the last dimension only, so
    every position of every sequence is normalized on its own; nothing mixes
    across positions or across the batch. The variance is the biased one (divide
    by ``d``, not ``d - 1``), which is the convention the converted weights were
    trained under.
    """

    def __init__(self, dim: int, eps: float = LAYER_NORM_EPS) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))
        self.bias = nn.Parameter(torch.zeros(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        variance, mean = torch.var_mean(x, dim=-1, unbiased=False, keepdim=True)
        return (x - mean) * torch.rsqrt(variance + self.eps) * self.weight + self.bias


def attention_mask(padding: torch.Tensor) -> torch.Tensor:
    """Which key each query may read: earlier-or-same positions that are real items.

    ``padding``: ``(batch, length)`` boolean, ``True`` where the input is the pad id.
    Returns ``(batch, 1, length, length)`` boolean, ``True`` where query ``i`` may
    attend to key ``j``. The singleton axis broadcasts over heads.

    Two masks, combined with AND:

    * **causal** — ``j <= i``, so a prediction at position ``i`` never sees a movie
      watched after it;
    * **padding** — key ``j`` is a real movie, so the zero-filled left padding is
      never read.

    A real query always keeps at least one key (itself). A padded query under left
    padding keeps none, which is the case ``scaled_dot_product`` handles.
    """
    length = padding.shape[1]
    causal = torch.ones(length, length, dtype=torch.bool, device=padding.device).tril()
    return causal.view(1, 1, length, length) & ~padding.view(-1, 1, 1, length)


def scaled_dot_product(
    query: torch.Tensor,
    key: torch.Tensor,
    value: torch.Tensor,
    allowed: torch.Tensor,
    dropout: nn.Dropout,
) -> torch.Tensor:
    """Attention for every head at once, defined even for a row with nothing to read.

    ``query``, ``key``, ``value``: ``(batch, heads, length, head_dim)``.
    ``allowed``: ``(batch, 1, length, length)``. Returns ``(batch, heads, length, head_dim)``.

    The usual recipe — fill forbidden scores with ``-inf`` and take a softmax —
    divides zero by zero on a row where every key is forbidden, and the NaN it
    produces poisons every later layer that reads the row. Such rows exist for
    every left-padded position. So a row with no allowed key is given finite
    scores before the softmax and has its weights zeroed after it: its output is
    exactly zero, and its gradient is exactly zero too. Rows that do have a key
    are untouched — their forbidden weights come out of the softmax as exact
    zeros either way, which is what makes the result match the v1 encoder's.
    """
    scale = 1.0 / math.sqrt(query.shape[-1])
    scores = (query @ key.transpose(-2, -1)) * scale
    scores = scores.masked_fill(~allowed, float("-inf"))
    has_key = allowed.any(dim=-1, keepdim=True)
    scores = scores.masked_fill(~has_key, 0.0)
    weights = torch.softmax(scores, dim=-1).masked_fill(~allowed, 0.0)
    attended: torch.Tensor = dropout(weights) @ value
    return attended


class MultiHeadSelfAttention(nn.Module):
    """Self-attention split across ``heads`` independent subspaces.

    Four ``d -> d`` projections: query, key and value make the three views of the
    input, and output recombines the heads. Head ``h`` owns columns
    ``h * head_dim`` to ``(h + 1) * head_dim`` of each projection — the same
    slicing the v1 weights were trained with, which is what lets one packed v1
    matrix split cleanly into ``query``, ``key`` and ``value``.
    """

    def __init__(self, dim: int, heads: int, dropout: float) -> None:
        super().__init__()
        if dim % heads:
            raise ValueError("dim must be divisible by heads")
        self.heads = heads
        self.head_dim = dim // heads
        self.query = _linear(dim, dim)
        self.key = _linear(dim, dim)
        self.value = _linear(dim, dim)
        self.output = _linear(dim, dim)
        self.weight_dropout = nn.Dropout(dropout)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        """v1's initialization, draw for draw.

        v1 built its output projection first (Kaiming-uniform weight, uniform
        bias), then drew one Xavier-uniform ``(3d, d)`` matrix holding query, key
        and value stacked, then zeroed every bias. Drawing the stacked matrix and
        slicing it, rather than drawing three ``(d, d)`` matrices, keeps both the
        random stream and the Xavier bound (which depends on the matrix shape)
        identical.
        """
        dim = self.heads * self.head_dim
        self.output.reset_parameters()
        stacked = torch.empty(3 * dim, dim)
        nn.init.xavier_uniform_(stacked)
        with torch.no_grad():
            for index, projection in enumerate((self.query, self.key, self.value)):
                projection.weight.copy_(stacked[index * dim : (index + 1) * dim])
                projection.bias.zero_()
            self.output.bias.zero_()

    def _split_heads(self, x: torch.Tensor) -> torch.Tensor:
        batch, length, _ = x.shape
        return x.view(batch, length, self.heads, self.head_dim).transpose(1, 2)

    def forward(self, x: torch.Tensor, allowed: torch.Tensor) -> torch.Tensor:
        """``x``: ``(batch, length, d)`` -> ``(batch, length, d)``."""
        batch, length, dim = x.shape
        attended = scaled_dot_product(
            self._split_heads(self.query(x)),
            self._split_heads(self.key(x)),
            self._split_heads(self.value(x)),
            allowed,
            self.weight_dropout,
        )
        merged = attended.transpose(1, 2).reshape(batch, length, dim)
        output: torch.Tensor = self.output(merged)
        return output


class FeedForward(nn.Module):
    """The per-position MLP: widen, GELU, dropout, narrow back.

    ``(batch, length, d) -> (batch, length, hidden) -> (batch, length, d)``. It
    looks at one position at a time; all mixing across positions happens in
    attention.
    """

    def __init__(self, dim: int, hidden: int, dropout: float) -> None:
        super().__init__()
        self.expand = _linear(dim, hidden)
        self.contract = _linear(hidden, dim)
        self.dropout = nn.Dropout(dropout)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        self.expand.reset_parameters()
        self.contract.reset_parameters()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output: torch.Tensor = self.contract(self.dropout(F.gelu(self.expand(x))))
        return output


class PreNormBlock(nn.Module):
    """One encoder block: two pre-normalized residual sub-layers.

    ``x = x + dropout(attention(norm(x)))`` then ``x = x + dropout(feed_forward(norm(x)))``.
    Normalizing *before* each sub-layer (rather than after the residual sum)
    leaves the residual path itself untouched, which is why the stack needs one
    more normalization at the very end — ``SASRecEncoder.output_norm``.
    """

    def __init__(self, dim: int, heads: int, hidden: int, dropout: float) -> None:
        super().__init__()
        # Construction order is initialization order: v1 drew attention's
        # weights, then the feed-forward's, and the norms draw nothing.
        self.attention = MultiHeadSelfAttention(dim, heads, dropout)
        self.feed_forward = FeedForward(dim, hidden, dropout)
        self.attention_norm = LayerNormalization(dim)
        self.feed_forward_norm = LayerNormalization(dim)
        self.attention_dropout = nn.Dropout(dropout)
        self.feed_forward_dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, allowed: torch.Tensor) -> torch.Tensor:
        x = x + self.attention_dropout(self.attention(self.attention_norm(x), allowed))
        output: torch.Tensor = x + self.feed_forward_dropout(
            self.feed_forward(self.feed_forward_norm(x))
        )
        return output


class TransformerStack(nn.Module):
    """``num_blocks`` pre-norm blocks with one causal-and-padding mask shared by all.

    Every block starts as a copy of the same freshly initialized block. That is
    not a design preference; it is what v1's encoder did (it deep-copied one layer
    ``num_blocks`` times), and keeping it is what makes a fresh stack identical to
    a fresh v1 stack. Training moves the copies apart within the first step.
    """

    def __init__(self, *, dim: int, heads: int, hidden: int, num_blocks: int, dropout: float):
        super().__init__()
        prototype = PreNormBlock(dim, heads, hidden, dropout)
        self.blocks = nn.ModuleList(copy.deepcopy(prototype) for _ in range(num_blocks))

    def forward(self, x: torch.Tensor, padding: torch.Tensor) -> torch.Tensor:
        """``x``: ``(batch, length, d)``; ``padding``: ``(batch, length)`` boolean.

        Padded rows are set to zero on the way in and after every block. Real rows
        never read them — the mask gives them weight exactly zero — so this changes
        no real output; what it buys is that a padded row cannot carry a large or
        non-finite value into a later layer, and that the stack's padded output is
        zeros by construction rather than by a final clean-up.
        """
        allowed = attention_mask(padding)
        pad_rows = padding.unsqueeze(-1)
        x = x.masked_fill(pad_rows, 0.0)
        for block in self.blocks:
            x = block(x, allowed).masked_fill(pad_rows, 0.0)
        return x
