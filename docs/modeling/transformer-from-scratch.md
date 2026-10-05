# The SASRec Transformer, written out by hand

SASRec reads a user's last 50 movies, oldest first, and produces one vector per
position. The vector at the last position is the user's query: retrieval returns
the 500 movies whose embeddings have the largest dot product with it.

Until WO-2 the middle of that model was PyTorch's packaged encoder
(`nn.TransformerEncoderLayer` stacked by `nn.TransformerEncoder`, plus
`nn.LayerNorm`). It is now
[`src/models/candidates/transformer.py`](../../src/models/candidates/transformer.py):
about 270 lines, most of them comments, of `nn.Linear`, `nn.Dropout`,
`nn.Parameter` and tensor arithmetic, following rule D7 of the 2026-10-05 build
brief. This page walks through it in the
order data flows, so every line can be explained without notes.

## Sizes used throughout

The SASRec v1 configuration, so every shape below has real numbers in it:

| Symbol | Meaning | v1 value |
|---|---|---|
| `B` | sequences in a batch | 1 when serving one user; up to 512 targets per step in training |
| `L` | window length (`max_sequence_length`) | 50 |
| `d` | width (`hidden_dim`) | 64 |
| `H` | attention heads (`num_heads`) | 2 |
| `d_h` | width per head, `d / H` | 32 |
| `F` | feed-forward width (`feedforward_dim`) | 256 |
| `N` | blocks (`num_blocks`) | 2 |

## The input: left-padded ids

A user with 12 movies becomes a row of 50 integers: 38 zeros, then the 12 movie
ids in the order they were watched. Id 0 is padding; ids 1 to 34,461 are movies;
34,462 is the "unknown movie" token for anything released after training.

```
sequences: (B, L) integers      e.g. [0, 0, ..., 0, 812, 4410, ..., 977]
padding  : (B, L) booleans      sequences == 0, True on the 38 left slots
```

Padding goes on the **left** so the newest movie is always at position 49, the one
position retrieval reads.

## Step 1 — embeddings (`SASRecEncoder.encode_positions`)

```
values = item_embedding(sequences) + position_embedding(0..L-1)    (B, L, d)
```

Each id looks up a learned 64-number vector, and each slot 0–49 has its own learned
vector added so the model knows order. Row 0 of the item table is held at zero
(`padding_idx=0`), and the same table is reused at the output to score movies
("tied" embeddings). There is no `sqrt(d)` scaling and no embedding dropout — v1
had neither, and matching v1 exactly is what lets saved models keep working.

## Step 2 — the two masks (`attention_mask`)

Attention lets each position read other positions. Two rules say which:

- **Causal mask** — position `i` may read position `j` only if `j <= i`. A
  prediction at position 30 must not see movie 31, or training would leak the
  answer. As a matrix it is lower-triangular: `ones(L, L).tril()`.
- **Padding mask** — position `j` may be read only if it is a real movie. The 38
  zeros carry no information and must not dilute anyone's attention.

They are combined with AND into one boolean tensor:

```
allowed = causal.view(1, 1, L, L) & ~padding.view(B, 1, 1, L)      (B, 1, L, L)
```

`allowed[b, 0, i, j]` answers "may query `i` read key `j` in sequence `b`". The
singleton second axis broadcasts across heads, because every head obeys the same
rules.

One consequence matters a great deal. Under left padding, a padded query position
(say position 5 of the 12-movie user) may read only positions 0–5, and all of those
are padding, so its row of `allowed` is **entirely False**. Every left-padded
sequence has such rows. Step 4 explains why that used to break the model.

## Step 3 — layer normalization (`LayerNormalization`)

```
mean, var = mean and biased variance of x over its last axis      (B, L, 1) each
y = (x - mean) / sqrt(var + 1e-5) * weight + bias                   (B, L, d)
```

Every position's 64 numbers are shifted to mean 0 and scaled to variance 1, then
stretched and shifted by two learned 64-vectors. It works on one position at a
time, so nothing mixes across positions or users. "Biased" means dividing by `d`,
not `d − 1`, and `eps = 1e-5` keeps the division safe; both are what v1 used.

## Step 4 — attention for every head at once (`scaled_dot_product`)

Inputs are already split into heads (Step 5 does the split):

```
query, key, value : (B, H, L, d_h)
scores  = query @ key^T * (1 / sqrt(d_h))                          (B, H, L, L)
scores  = -inf wherever allowed is False
weights = softmax(scores over the last axis)                        (B, H, L, L)
output  = dropout(weights) @ value                                  (B, H, L, d_h)
```

`scores[b, h, i, j]` is how strongly query `i` matches key `j`. Dividing by
`sqrt(32)` keeps the scores from growing with the width, so the softmax does not
saturate. Setting forbidden scores to `-inf` makes their softmax weight exactly 0.
Each query's weights then sum to 1, and its output is the weighted average of the
values it is allowed to read. In training, dropout zeroes some weights at random.

**Rows with nothing to read.** For a fully forbidden row the softmax computes
`exp(-inf) / sum(exp(-inf)) = 0 / 0 = NaN`. A NaN at a padded position looks
harmless, because no real position reads it. It is not: the next block computes
`weight × value` for every key, and `0 × NaN = NaN`, so at two blocks the NaN
reaches the last position and the user's query vector is NaN. That is what
PyTorch's inference fast path did (O-9, 2026-09-05): 131 warm users received
empty slates, and the fix was a process-wide switch turning that path off for the whole
Python process. The hand-written function handles the case in two lines:

```python
has_key = allowed.any(dim=-1, keepdim=True)        # (B, 1, L, 1)
scores  = scores.masked_fill(~has_key, 0.0)        # finite row, softmax well defined
weights = softmax(scores).masked_fill(~allowed, 0)  # ...then zeroed again
```

A row with no key gets ordinary finite scores, the softmax is well defined, and
the weights are then zeroed, so the output is exactly 0 and so is its gradient.
Rows that do have a key are unchanged, because their forbidden weights were
already exactly 0.

## Step 5 — multi-head attention (`MultiHeadSelfAttention`)

```
q = query(x), k = key(x), v = value(x)        each (B, L, d)        nn.Linear(d, d)
split:  (B, L, d) -> (B, L, H, d_h) -> (B, H, L, d_h)                view + transpose
attend: scaled_dot_product(q, k, v, allowed)  (B, H, L, d_h)
merge:  (B, H, L, d_h) -> (L, B, H, d_h) -> (L, B, d)                permute + reshape
out   = output(merged), viewed as (B, L, d)   (B, L, d)             nn.Linear(d, d)
```

Three learned projections give three views of each position: what it is looking
for (query), what it offers to be matched on (key), and what it passes along
(value). Splitting 64 into 2 heads of 32 lets each head learn a different kind of
match; head `h` owns components `32h` to `32h + 31` of each projected vector
(rows `32h` to `32h + 31` of each weight matrix). The output projection mixes the
heads back together.

The heads are merged in `(L, B, d)` memory order and handed back as a `(B, L, d)`
view. The values are identical either way. What differs is memory order, and
dropout draws its random mask in memory order. PyTorch's attention returned this
layout, so the residual dropout that follows lands on the same elements at the
same seed. Without it the masks were the same random numbers laid on different
elements: a statistically identical model, but a different training trajectory
from the same seed. The owner's training-parity check on 2026-10-05 found this
(`test_same_seed_training_steps_match_the_packaged_encoder`).

## Step 6 — the feed-forward layer (`FeedForward`)

```
x -> expand: Linear(d, F) -> GELU -> dropout -> contract: Linear(F, d)
(B, L, 64) -> (B, L, 256) -> (B, L, 256) -> (B, L, 64)
```

A two-layer MLP applied to each position on its own. Attention moves information
between positions; this layer transforms it within a position. GELU is the exact
(erf-based) version, as in v1.

## Step 7 — the pre-norm residual block (`PreNormBlock`)

```
x = x + dropout(attention(attention_norm(x), allowed))
x = x + dropout(feed_forward(feed_forward_norm(x)))
```

Each sub-layer adds its output to its input (the residual), so a block can only
change the representation by adding to it, which keeps deep stacks trainable.
"Pre-norm" means the normalization runs **before** each sub-layer, on a copy;
the residual stream itself is never normalized inside the block. That is v1's
`norm_first=True`. Dropout sits in four places, as in v1: on the attention
weights, on the attention output, after GELU, and on the feed-forward output.

## Step 8 — the stack (`TransformerStack`)

```
x = x with padded rows set to 0                                     (B, L, d)
for each of the N blocks:
    x = block(x, allowed), then padded rows set to 0 again
```

The mask is built once and shared by every block. Setting padded rows to zero
changes nothing for real positions, which never read them (their weight is
exactly 0). It guarantees the **padding rule**: a padded position is always exact
zeros and can never carry a NaN or a large value into the next block. The
guarantee comes from this code, not from a switch somewhere else in the process.

All blocks start as copies of one freshly initialized block. That is what
`nn.TransformerEncoder` did (it deep-copies its layer `N` times), and it is kept
so that initialization matches v1 (next section). Training separates the copies
after the first step.

## Step 9 — output

```
encoded = output_norm(stack(values, padding))                      (B, L, d)
encoded = 0 at padded positions
query   = L2-normalize(encoded[:, -1, :])                          (B, d)
```

Because the blocks are pre-norm, the stream leaving the last block has never been
normalized, so one final layer norm (`output_norm`) is applied. Retrieval takes
the last position, scales it to length 1, and searches the item table by inner
product. Training scores the unnormalized last-position vector against the
positive movie and sampled negatives.

## Initialization: the same random draws as v1

`reset_parameters` in each module draws weights in the order v1's PyTorch classes
did:

1. item embedding, then position embedding (normal);
2. per block, the attention output projection (Kaiming-uniform weight, uniform
   bias), then **one** Xavier-uniform `(3d, d)` matrix sliced into query, key and
   value, then every attention bias zeroed;
3. the feed-forward `expand` then `contract` layers (PyTorch's default `nn.Linear`
   initialization);
4. layer-norm weights to 1 and biases to 0 (no random draws);
5. the item embedding redrawn at `std = 1/sqrt(d)`, with the padding row zeroed.

Drawing the stacked `(3d, d)` matrix, rather than three `(d, d)` ones, matters
twice: Xavier's bound depends on the matrix shape, and every later draw depends on
how many numbers came before it. The `nn.Linear` layers are created on PyTorch's
`meta` device so that creating them draws nothing. The result:
`torch.manual_seed(s)` followed by building either encoder gives **bit-identical**
starting weights (`test_a_fresh_encoder_is_a_fresh_v1_encoder_bit_for_bit`).

## Converting saved v1 weights

Every archive written before WO-2 stores the packaged layout's names.
`legacy_state_to_hand_written` in
[`sasrec_artifact.py`](../../src/models/candidates/sasrec_artifact.py) renames them
on load, in memory. No value changes: the one non-rename is a row slice.

| v1 name (block `i`) | Shape | Hand-written name (block `i`) |
|---|---|---|
| `transformer.layers.i.self_attn.in_proj_weight` rows `0:d` | `(d, d)` | `transformer.blocks.i.attention.query.weight` |
| `transformer.layers.i.self_attn.in_proj_weight` rows `d:2d` | `(d, d)` | `transformer.blocks.i.attention.key.weight` |
| `transformer.layers.i.self_attn.in_proj_weight` rows `2d:3d` | `(d, d)` | `transformer.blocks.i.attention.value.weight` |
| `transformer.layers.i.self_attn.in_proj_bias` `0:d` / `d:2d` / `2d:3d` | `(d,)` each | `transformer.blocks.i.attention.{query,key,value}.bias` |
| `transformer.layers.i.self_attn.out_proj.weight` / `.bias` | `(d, d)` / `(d,)` | `transformer.blocks.i.attention.output.weight` / `.bias` |
| `transformer.layers.i.linear1.weight` / `.bias` | `(F, d)` / `(F,)` | `transformer.blocks.i.feed_forward.expand.weight` / `.bias` |
| `transformer.layers.i.linear2.weight` / `.bias` | `(d, F)` / `(d,)` | `transformer.blocks.i.feed_forward.contract.weight` / `.bias` |
| `transformer.layers.i.norm1.weight` / `.bias` | `(d,)` | `transformer.blocks.i.attention_norm.weight` / `.bias` |
| `transformer.layers.i.norm2.weight` / `.bias` | `(d,)` | `transformer.blocks.i.feed_forward_norm.weight` / `.bias` |
| `item_embedding.weight`, `position_embedding.weight`, `output_norm.weight`, `output_norm.bias` | unchanged | unchanged |

The converter is strict: an unknown name, a block beyond `num_blocks`, or a block
missing any tensor is refused, because a silently dropped tensor would leave that
weight at its random starting value and still load.

**What changes on disk.** Nothing for existing archives: their bytes, SHA-256 and
manifest are untouched, which matters because published bundles pin those
checksums. The archive format (`schema_version` 1, stored zip, fixed member order,
one `.npy` per tensor) is unchanged. A **new** export records
`"encoder_impl": "hand-written-transformer-v1"` in its metadata and manifest and
stores the hand-written names; an archive with no `encoder_impl` is read as
`"torch-packaged-encoder-v1"` and converted. Both load into the same hand-written
encoder. See [`sasrec-artifacts.md`](sasrec-artifacts.md).

## How close is "the same"?

The converted encoder computes the same function as v1 with different
floating-point kernels, so results agree to rounding error, not bit for bit.
`tests/unit/test_sasrec_transformer.py` copies weights out of a real
`nn.TransformerEncoder` (which lives only in `tests/`) and checks every position
of left-padded histories of length 1, 3, 12, 49 and 50:

| Comparison | Mode | Largest absolute difference observed | Test bound |
|---|---|---|---|
| Perturbed weights, 3 seeds | `eval()` | 2.4e-6 | 1e-5 |
| Perturbed weights | `train()`, dropout 0 | 2.4e-6 (gradients within rtol 1e-4) | 1e-5 |
| Weights learned by the real trainer | `eval()` | 1.4e-6 | 1e-5 |

Absolute error grows with activation size. With weights perturbed three times
harder than the test uses (noise 0.3 on every parameter), outputs reached 5.5 in
magnitude and the gap reached 1.2e-5 to 2.3e-5 depending on the draw, about
2e-6 to 4e-6 relative. The real full-data model is measured directly by
the held inference check (WO-2 done-criterion 3).

Two consequences:

- A saved model scores the same up to near-ties. A user's 500th and 501st
  candidates can trade places when their scores differ by less than the rounding
  error, which is why done-criterion 3 asks for warm recall to 4 decimals and
  counts the changed lists, while cold must match exactly (cold users go to the
  popularity fallback and never reach the encoder).
- **Training matches step for step.** From the same weights and the same seed,
  with dropout on, both encoders consume the random generator identically, draw
  the same dropout masks, and give the same loss and gradients. On the first three
  real batches of the 6% pilot the largest gradient difference was 1.1e-8 in
  float32 and 2.4e-17 in float64.
- **Retraining v1 now gives an equivalent model, not a bit-identical one.**
  Initialization is identical, but each training step rounds a little differently
  and dropout draws its masks in a different order, so the weights drift apart over
  thousands of steps. Results should land within seed-to-seed spread; they will not
  reproduce a pre-WO-2 checksum.

## Where each claim is tested

| Claim | Test (all in `tests/unit/test_sasrec_transformer.py` unless noted) |
|---|---|
| No packaged Transformer code under `src/` | `test_src_uses_no_packaged_transformer_code` |
| Same outputs as v1 | `test_eval_outputs_match_the_packaged_encoder`, `test_train_mode_without_dropout_matches_outputs_and_gradients`, `test_learned_weights_match_through_the_packaged_encoder` |
| Same initialization as v1 | `test_a_fresh_encoder_is_a_fresh_v1_encoder_bit_for_bit` |
| Same training step as v1 (loss, gradients, dropout masks, generator use) | `test_same_seed_training_steps_match_the_packaged_encoder`, `test_dropout_sites_and_rates_match_the_packaged_encoder`; on real pilot batches `test_training_parity_on_three_real_pilot_batches` (data-gated) |
| Old archives load and retrieve the same | `test_a_pre_wo2_archive_loads_and_retrieves_what_the_old_encoder_retrieved`; `test_a_pre_wo2_archive_serves_what_the_offline_model_retrieves` in `test_sidecar_sasrec_load.py` |
| Can learn | `test_memorization_recovers_a_deterministic_next_movie` |
| Every weight trains | `test_every_weight_receives_a_gradient` |
| Causal | `test_a_later_movie_cannot_change_an_earlier_output` |
| Padding is zeros, never NaN | `test_padded_positions_are_exact_zeros_and_never_nan`, `test_attention_on_a_row_with_nothing_to_read_is_zero_not_nan`, `test_an_entirely_padded_sequence_encodes_to_zeros_with_finite_gradients` |
| 500 candidates at lengths 1, 3, 12, 49, 50 | `test_two_block_eval_retrieves_500_for_every_supported_history_length` in `test_sasrec.py` |
