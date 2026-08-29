"""
Exploratory data analysis on MovieLens 25M in Postgres.

Re-runnable from the project root: `python -m notebooks.eda` (or `make eda`).
Run with `-m` so the project root lands on sys.path and `src.config` resolves;
running `python notebooks/eda.py` directly will fail with ModuleNotFoundError.
Every section
runs as a SQL aggregation so result sets are tiny — we never pull 25 M rows
into pandas. Output is plain text designed to be pasted into docs/eda.md
when iterating on the writeup.

The last section renders the three figures docs/eda.md embeds into
docs/assets/eda/. They are drawn from the same aggregations the text sections
print, so a figure can never disagree with the table above it — the whole
reason they live here rather than in a one-off plotting script.

This script computes descriptive statistics only. It deliberately does not
compute any model metric — those go through src/evaluation/ per
non-negotiable #5. The split logic here mirrors src/data/split.py
(80th-percentile cutoff, 28-day holdout) but is expressed in SQL so the
numbers can be verified against the in-Python implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

# Chosen before pyplot is imported: the figures are files, never windows, and a
# GUI backend would make this script depend on whatever the machine happens to
# have installed.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402 — must follow matplotlib.use()
import pandas as pd  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from sqlalchemy import Connection, create_engine, text  # noqa: E402

from src.config import Settings  # noqa: E402

FIGURE_DIR = Path("docs/assets/eda")

# The figures are PNGs embedded in a Markdown file that GitHub renders on either
# a light or a dark page, and neither theme gets to supply the background. So
# every figure paints its own opaque light surface and every ink below is chosen
# against that surface rather than against the page.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
# Categorical slots 1–3. Used for train / holdout / test in the split figure;
# the other two figures show a single series and use slot 1 alone.
SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"
SERIES_3 = "#1baf7a"
# A recessive fill for the part of a split that is *not* the point being made.
NEUTRAL_FILL = "#d8d7d0"

# Deterministic rendering. DejaVu Sans ships inside matplotlib, so it is the one
# family that resolves identically on every machine; anything else would make
# the bytes depend on the host's font cache.
RC_PARAMS: dict[Any, Any] = {
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans"],
    "figure.dpi": 160,
    "savefig.dpi": 160,
    "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK_SECONDARY,
    "text.color": INK,
    "xtick.color": INK_MUTED,
    "ytick.color": INK_MUTED,
    "xtick.labelcolor": INK_SECONDARY,
    "ytick.labelcolor": INK_SECONDARY,
    "grid.color": GRID,
    "axes.grid": False,
    "svg.hashsalt": "movielens-eda",
}

# matplotlib stamps a "Software: Matplotlib version …" tag into every PNG it
# writes. Suppressing it is what lets a re-render on a different matplotlib
# patch release produce identical bytes, which is the whole claim docs/eda.md
# makes about these files.
PNG_METADATA: dict[str, str | None] = {"Software": None}


def main() -> None:
    settings = Settings()
    engine = create_engine(settings.database_url)

    with engine.connect() as conn:
        _section("1. Scale & sparsity", scale_stats(conn))
        counts = rating_counts(conn)
        _section("2. Rating distribution", rating_distribution(counts))
        _section("3. User activity (ratings per user)", user_activity_distribution(conn))
        _section("4. Item popularity (ratings per movie)", item_popularity_distribution(conn))
        _section("5. Top 10 most-rated movies", most_rated_movies(conn))
        span = temporal_range(conn)
        _section("6. Temporal range", span)
        boundary = split_boundary(conn)
        _section("7. Split boundary T (per ADR 0001)", boundary)
        sizes = split_sizes(conn)
        _section("8. Split sizes", sizes)
        cold = cold_start_sizing(conn)
        _section("9. Cold-start sizing", cold)

        popularity = movie_rating_counts(conn)
        catalog_size = int(conn.execute(text("SELECT COUNT(*) FROM movies")).scalar_one())

    print("\n## 10. Figures\n")
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    with plt.rc_context(RC_PARAMS):
        written = [
            render_rating_histogram(counts, FIGURE_DIR / "rating-histogram.png"),
            render_item_popularity_tail(
                popularity, catalog_size, FIGURE_DIR / "item-popularity-tail.png"
            ),
            render_temporal_split(span, boundary, sizes, cold, FIGURE_DIR / "temporal-split.png"),
        ]
    for path in written:
        print(f"wrote {path}")
    print()


def _section(title: str, df: pd.DataFrame) -> None:
    print(f"\n## {title}\n")
    # to_string keeps the markdown-friendly fixed-width look in a terminal.
    print(df.to_string(index=False))
    print()


def scale_stats(conn: Connection) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for table in ("ratings", "movies", "tags", "links"):
        n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        rows.append({"metric": f"{table} rows", "value": f"{int(n):,}"})

    n_users = conn.execute(text('SELECT COUNT(DISTINCT "userId") FROM ratings')).scalar_one()
    n_movies_rated = conn.execute(
        text('SELECT COUNT(DISTINCT "movieId") FROM ratings')
    ).scalar_one()
    n_movies_total = conn.execute(text("SELECT COUNT(*) FROM movies")).scalar_one()

    sparsity = float(n_users) * float(n_movies_rated)
    sparsity_pct = conn.execute(text("SELECT COUNT(*) FROM ratings")).scalar_one() / sparsity * 100

    rows.extend(
        [
            {"metric": "distinct users in ratings", "value": f"{int(n_users):,}"},
            {"metric": "distinct movies in ratings", "value": f"{int(n_movies_rated):,}"},
            {
                "metric": "movies in catalog never rated",
                "value": f"{int(n_movies_total) - int(n_movies_rated):,}",
            },
            {"metric": "sparsity (filled cells)", "value": f"{sparsity_pct:.4f}%"},
        ]
    )
    return pd.DataFrame(rows)


def rating_counts(conn: Connection) -> pd.DataFrame:
    """Raw half-star histogram — one row per rating value, counts unformatted.

    Kept separate from ``rating_distribution`` so section 2's table and the
    figure it captions are computed from the same scan and cannot drift.
    """
    return pd.read_sql(
        text("SELECT rating, COUNT(*) AS count FROM ratings GROUP BY rating ORDER BY rating"),
        conn,
    )


def rating_distribution(counts: pd.DataFrame) -> pd.DataFrame:
    df = counts.copy()
    df["pct"] = (df["count"] / df["count"].sum() * 100).round(2)
    df["count"] = df["count"].map(lambda n: f"{n:,}")
    return df


def user_activity_distribution(conn: Connection) -> pd.DataFrame:
    row = conn.execute(text("""
            WITH per_user AS (
                SELECT "userId", COUNT(*) AS n FROM ratings GROUP BY "userId"
            )
            SELECT
                MIN(n) AS min,
                percentile_disc(0.25) WITHIN GROUP (ORDER BY n) AS p25,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY n) AS p50,
                percentile_disc(0.75) WITHIN GROUP (ORDER BY n) AS p75,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY n) AS p95,
                percentile_disc(0.99) WITHIN GROUP (ORDER BY n) AS p99,
                MAX(n) AS max,
                ROUND(AVG(n), 1) AS mean
            FROM per_user
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def item_popularity_distribution(conn: Connection) -> pd.DataFrame:
    row = conn.execute(text("""
            WITH per_movie AS (
                SELECT "movieId", COUNT(*) AS n FROM ratings GROUP BY "movieId"
            )
            SELECT
                MIN(n) AS min,
                percentile_disc(0.25) WITHIN GROUP (ORDER BY n) AS p25,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY n) AS p50,
                percentile_disc(0.75) WITHIN GROUP (ORDER BY n) AS p75,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY n) AS p95,
                percentile_disc(0.99) WITHIN GROUP (ORDER BY n) AS p99,
                MAX(n) AS max,
                ROUND(AVG(n), 1) AS mean
            FROM per_movie
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def most_rated_movies(conn: Connection) -> pd.DataFrame:
    return pd.read_sql(
        text("""
            SELECT
                m.title,
                COUNT(*) AS n_ratings,
                ROUND(AVG(r.rating)::numeric, 2) AS avg_rating
            FROM ratings r JOIN movies m USING ("movieId")
            GROUP BY m.title
            ORDER BY n_ratings DESC
            LIMIT 10
            """),
        conn,
    )


def temporal_range(conn: Connection) -> pd.DataFrame:
    row = conn.execute(text("""
            SELECT
                to_timestamp(MIN(timestamp)) AS earliest,
                to_timestamp(MAX(timestamp)) AS latest,
                (MAX(timestamp) - MIN(timestamp)) / 86400 AS span_days,
                ROUND(((MAX(timestamp) - MIN(timestamp)) / 86400 / 365.0)::numeric, 1)
                    AS span_years
            FROM ratings
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def split_boundary(conn: Connection) -> pd.DataFrame:
    # percentile_disc(0.8) selects an actual value present in the data, matching
    # the method="lower" choice in src/data/split.py. The two numbers must agree.
    row = conn.execute(text("""
            WITH t AS (
                SELECT percentile_disc(0.8) WITHIN GROUP (ORDER BY timestamp)::bigint
                    AS cutoff
                FROM ratings
            )
            SELECT
                cutoff,
                to_timestamp(cutoff) AS cutoff_dt,
                cutoff + 28*86400 AS holdout_end,
                to_timestamp(cutoff + 28*86400) AS holdout_end_dt
            FROM t
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def split_sizes(conn: Connection) -> pd.DataFrame:
    # One scan over ratings cross-joined with the 1-row cutoff CTE.
    row = conn.execute(text("""
            WITH t AS (
                SELECT percentile_disc(0.8) WITHIN GROUP (ORDER BY timestamp)::bigint
                    AS cutoff
                FROM ratings
            )
            SELECT
                SUM(CASE WHEN r.timestamp < t.cutoff THEN 1 ELSE 0 END) AS train,
                SUM(CASE
                    WHEN r.timestamp >= t.cutoff
                     AND r.timestamp < t.cutoff + 28*86400 THEN 1 ELSE 0 END) AS holdout,
                SUM(CASE WHEN r.timestamp >= t.cutoff + 28*86400 THEN 1 ELSE 0 END)
                    AS test
            FROM ratings r CROSS JOIN t
            """)).mappings().one()
    df = pd.DataFrame([dict(row)])
    total = int(df["train"].iloc[0]) + int(df["holdout"].iloc[0]) + int(df["test"].iloc[0])
    df["total"] = total
    for col in ("train", "holdout", "test"):
        df[f"{col}_pct"] = (df[col].astype(float) / total * 100).round(2)
    return df


def cold_start_sizing(conn: Connection) -> pd.DataFrame:
    # The breakdown that matters for the eval harness: how many warm vs cold
    # vs brand-new users we'll be scoring. Drives expectations for the per-slice
    # metrics that protocol.py reports.
    row = conn.execute(text("""
            WITH t AS (
                SELECT percentile_disc(0.8) WITHIN GROUP (ORDER BY timestamp)::bigint
                    AS cutoff
                FROM ratings
            ),
            train_counts AS (
                SELECT r."userId", COUNT(*) AS n
                FROM ratings r CROSS JOIN t
                WHERE r.timestamp < t.cutoff
                GROUP BY r."userId"
            ),
            holdout_users AS (
                SELECT DISTINCT r."userId"
                FROM ratings r CROSS JOIN t
                WHERE r.timestamp >= t.cutoff
                  AND r.timestamp < t.cutoff + 28*86400
            )
            SELECT
                (SELECT COUNT(*) FROM train_counts WHERE n >= 5) AS warm_in_train,
                (SELECT COUNT(*) FROM train_counts WHERE n < 5) AS cold_in_train,
                (SELECT COUNT(*) FROM holdout_users) AS total_in_holdout,
                (SELECT COUNT(*) FROM holdout_users h
                 WHERE NOT EXISTS (
                     SELECT 1 FROM train_counts tc WHERE tc."userId" = h."userId"
                 )) AS new_in_holdout,
                (SELECT COUNT(*) FROM holdout_users h
                 JOIN train_counts tc ON tc."userId" = h."userId"
                 WHERE tc.n < 5) AS cold_in_holdout,
                (SELECT COUNT(*) FROM holdout_users h
                 JOIN train_counts tc ON tc."userId" = h."userId"
                 WHERE tc.n >= 5) AS warm_in_holdout
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def movie_rating_counts(conn: Connection) -> pd.DataFrame:
    """Ratings per movie, most-rated first — one row per movie that has any.

    ~59 k rows, which is small enough to bring back whole. It is the only query
    here that returns a row per entity rather than an aggregate, and it exists
    because a percentile summary cannot show the shape of a tail.
    """
    return pd.read_sql(
        text('SELECT COUNT(*) AS n FROM ratings GROUP BY "movieId" ORDER BY n DESC'),
        conn,
    )


def render_rating_histogram(counts: pd.DataFrame, path: Path) -> Path:
    """Section 2's half-star distribution."""
    total = int(counts["count"].sum())
    pct = counts["count"].astype(float) / total * 100
    labels = [f"{v:g}" for v in counts["rating"]]
    positions = range(len(counts))
    mode_index = int(pct.idxmax())

    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    ax.bar(positions, pct, width=0.72, color=SERIES_1, linewidth=0)

    for i, value in enumerate(pct):
        ax.text(
            i,
            value + 0.6,
            f"{value:.2f}%",
            ha="center",
            va="bottom",
            fontsize=8.5,
            color=INK if i == mode_index else INK_SECONDARY,
            fontweight="bold" if i == mode_index else "normal",
        )

    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_xlabel("Rating (half-star scale)", fontsize=9.5, labelpad=8)
    ax.set_ylabel("Share of all ratings", fontsize=9.5, labelpad=8)
    ax.set_ylim(0, float(pct.max()) * 1.18)
    ax.yaxis.set_major_formatter(lambda v, _pos: f"{v:.0f}%")
    _recede(ax, y_grid=True)

    _titles(
        fig,
        "Ratings skew positive: the mode is 4.0",
        f"{total:,} ratings. {float(pct[counts['rating'] >= 4.0].sum()):.2f}% are 4.0 "
        f"or above, {float(pct[counts['rating'] == counts['rating'].min()].iloc[0]):.2f}% "
        "are half a star.",
    )
    return _save(fig, path, rect_top=0.85)


def render_item_popularity_tail(popularity: pd.DataFrame, catalog_size: int, path: Path) -> Path:
    """Section 4's long tail, as a rank–frequency plot on log–log axes."""
    n = popularity["n"].to_numpy()
    rank = range(1, len(n) + 1)
    median = float(pd.Series(n).median())
    mean = float(pd.Series(n).mean())
    never_rated = catalog_size - len(n)

    fig, ax = plt.subplots(figsize=(8.0, 4.6))
    ax.plot(list(rank), n, color=SERIES_1, linewidth=2.0, solid_capstyle="round")

    # The median is the claim the percentile table makes; drawing it shows how
    # much of the catalog sits at or below it.
    ax.axhline(median, color=INK_MUTED, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.text(
        1.4,
        median * 1.25,
        f"median {median:,.0f} ratings",
        fontsize=8.5,
        color=INK_SECONDARY,
        va="bottom",
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(1, len(n) * 1.05)
    ax.set_xlabel("Movie rank by number of ratings (log)", fontsize=9.5, labelpad=8)
    ax.set_ylabel("Ratings received (log)", fontsize=9.5, labelpad=8)
    _recede(ax, y_grid=True, x_grid=True)

    # The title says what the curve shows, not what the shape was assumed to be:
    # on log–log this is visibly convex, with a flatter head and a steeper fall
    # than a straight power law, so calling it one here would be contradicted by
    # the figure directly underneath the claim.
    _titles(
        fig,
        f"Half the rated catalog has {median:,.0f} ratings or fewer",
        f"{len(n):,} rated movies — median {median:,.0f}, mean {mean:,.1f}, max {int(n[0]):,}.\n"
        f"A further {never_rated:,} catalog titles have no ratings at all, so they cannot appear "
        "on a log axis at any position.",
    )
    return _save(fig, path, rect_top=0.80)


def render_temporal_split(
    span: pd.DataFrame,
    boundary: pd.DataFrame,
    sizes: pd.DataFrame,
    cold: pd.DataFrame,
    path: Path,
) -> Path:
    """Sections 6–9 in one picture: the split in calendar time, in rows, in users.

    Three panels rather than one, because the split is tiny on one axis and
    large on another: the holdout is 28 days out of 25 years but 129 683
    interactions, and neither fact is visible in the other's units.
    """
    earliest = _to_naive_utc(span["earliest"].iloc[0])
    latest = _to_naive_utc(span["latest"].iloc[0])
    cutoff = _epoch_to_naive_utc(int(boundary["cutoff"].iloc[0]))
    holdout_end = _epoch_to_naive_utc(int(boundary["holdout_end"].iloc[0]))

    train_rows = int(sizes["train"].iloc[0])
    holdout_rows = int(sizes["holdout"].iloc[0])
    test_rows = int(sizes["test"].iloc[0])
    total_rows = train_rows + holdout_rows + test_rows

    holdout_users = int(cold["total_in_holdout"].iloc[0])
    cold_users = int(cold["new_in_holdout"].iloc[0]) + int(cold["cold_in_holdout"].iloc[0])
    warm_users = holdout_users - cold_users

    fig, (ax_time, ax_rows, ax_users) = plt.subplots(
        3, 1, figsize=(9.0, 6.2), height_ratios=[1.5, 1.0, 1.0]
    )

    # --- Panel 1: calendar time -------------------------------------------
    segments = [
        (earliest, cutoff, SERIES_1, "Train"),
        (cutoff, holdout_end, SERIES_2, "Holdout"),
        (holdout_end, latest, SERIES_3, "Test"),
    ]
    for start, end, color, label in segments:
        ax_time.barh(
            0, (end - start).days, left=start, height=0.44, color=color, linewidth=0, label=label
        )
    ax_time.set_ylim(-0.62, 1.55)
    ax_time.set_yticks([])
    ax_time.set_xlim(earliest, latest)
    ax_time.tick_params(axis="x", labelsize=9)
    # The 28-day holdout is under a third of a percent of the span, so on this
    # axis it is a hairline. Say where it is rather than pretending it is wide.
    ax_time.annotate(
        f"T = {cutoff:%Y-%m-%d %H:%M:%S} UTC — the 28-day holdout is this hairline",
        xy=(cutoff, 0.25),
        xytext=(cutoff - (cutoff - earliest) * 0.02, 0.72),
        fontsize=8.5,
        color=INK_SECONDARY,
        ha="right",
        va="center",
        arrowprops={"arrowstyle": "-", "color": AXIS, "linewidth": 1.0},
    )
    # Legend above the bar rather than below it: the year ticks own the space
    # underneath, and a legend laid over them is unreadable at any size.
    ax_time.legend(
        loc="lower left",
        bbox_to_anchor=(0.0, 0.82),
        ncol=3,
        frameon=False,
        fontsize=9,
        handlelength=1.1,
        handleheight=0.9,
        borderpad=0.0,
        labelcolor=INK_SECONDARY,
    )
    _recede(ax_time)
    ax_time.spines["left"].set_visible(False)

    # --- Panel 2: share of rows -------------------------------------------
    _stacked_share(
        ax_rows,
        [
            (train_rows, SERIES_1, f"Train  {train_rows:,}  ({train_rows / total_rows:.2%})"),
            (holdout_rows, SERIES_2, ""),
            (test_rows, SERIES_3, f"Test  {test_rows:,}  ({test_rows / total_rows:.2%})"),
        ],
        total_rows,
    )
    ax_rows.annotate(
        f"Holdout  {holdout_rows:,}  ({holdout_rows / total_rows:.2%})",
        xy=(train_rows + holdout_rows / 2, 0.22),
        xytext=(train_rows + holdout_rows / 2, 0.95),
        fontsize=8.5,
        color=INK_SECONDARY,
        ha="center",
        va="bottom",
        arrowprops={"arrowstyle": "-", "color": AXIS, "linewidth": 1.0},
    )
    ax_rows.set_title(
        f"Share of the {total_rows:,} interactions",
        fontsize=9.5,
        color=INK_SECONDARY,
        loc="left",
        pad=18,
    )

    # --- Panel 3: holdout users -------------------------------------------
    _stacked_share(
        ax_users,
        [
            (warm_users, NEUTRAL_FILL, f"Warm  {warm_users:,}"),
            (
                cold_users,
                SERIES_2,
                f"Cold  {cold_users:,}  ({cold_users / holdout_users:.1%})",
            ),
        ],
        holdout_users,
        label_ink=(INK_SECONDARY, INK),
    )
    ax_users.set_title(
        f"The {holdout_users:,} users evaluated, by training history (ADR 0001: cold is < 5)",
        fontsize=9.5,
        color=INK_SECONDARY,
        loc="left",
        pad=6,
    )

    _titles(
        fig,
        "The temporal split, in three units",
        "Train on the past, score the next 28 days, hold the rest back. "
        "No random split ever touches this data.",
        top=0.955,
    )
    fig.subplots_adjust(hspace=0.8, top=0.80, bottom=0.06, left=0.05, right=0.985)
    return _save(fig, path, tight=False)


def _stacked_share(
    ax: Axes,
    parts: list[tuple[int, str, str]],
    total: int,
    label_ink: tuple[str, ...] = (),
) -> None:
    """One 100%-wide horizontal bar, labelled inside each segment wide enough."""
    left = 0
    for i, (value, color, label) in enumerate(parts):
        # A 2px surface gap between adjacent fills, expressed in data units so
        # it survives the figure's fixed dpi.
        gap = total * 0.0016 if i else 0.0
        ax.barh(0, value - gap, left=left + gap, height=0.5, color=color, linewidth=0)
        if label:
            ink = label_ink[i] if i < len(label_ink) else INK_SECONDARY
            ax.text(
                left + value / 2,
                -0.52,
                label,
                fontsize=8.5,
                color=ink,
                ha="center",
                va="top",
            )
        left += value
    ax.set_xlim(0, total)
    ax.set_ylim(-0.85, 0.55)
    ax.set_xticks([])
    ax.set_yticks([])
    _recede(ax)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)


def _recede(ax: Axes, *, y_grid: bool = False, x_grid: bool = False) -> None:
    """Push the chrome behind the data: no box, hairline grid, thin baseline."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(0.8)
        ax.spines[side].set_color(AXIS)
    ax.tick_params(length=0, pad=5)
    if y_grid:
        ax.grid(axis="y", color=GRID, linewidth=0.8)
    if x_grid:
        ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _titles(fig: Figure, title: str, subtitle: str, *, top: float = 0.955) -> None:
    """Title block, top-anchored so a two-line subtitle grows down, not up.

    Both texts use ``va="top"`` deliberately: matplotlib anchors multi-line text
    on its last line by default, so a subtitle that wraps to two lines would
    climb into the title.
    """
    fig.suptitle(
        title,
        fontsize=12.5,
        color=INK,
        x=0.045,
        ha="left",
        y=top,
        va="top",
        fontweight="bold",
    )
    # A gap fixed in points rather than in figure fractions, so the block looks
    # the same on a 4-inch figure and a 6-inch one.
    gap = 24.0 / (fig.get_figheight() * 72.0)
    fig.text(0.045, top - gap, subtitle, fontsize=9, color=INK_SECONDARY, ha="left", va="top")


def _save(fig: Figure, path: Path, *, tight: bool = True, rect_top: float = 0.84) -> Path:
    if tight:
        fig.tight_layout(rect=(0.0, 0.0, 1.0, rect_top))
    fig.savefig(path, facecolor=SURFACE, metadata=PNG_METADATA)
    plt.close(fig)
    return path


def _to_naive_utc(value: Any) -> datetime:
    """Postgres hands back a tz-aware timestamp; the plot wants a plain one."""
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return datetime(
        int(stamp.year),
        int(stamp.month),
        int(stamp.day),
        int(stamp.hour),
        int(stamp.minute),
        int(stamp.second),
    )


def _epoch_to_naive_utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC).replace(tzinfo=None)


if __name__ == "__main__":
    main()
