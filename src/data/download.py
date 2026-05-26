"""
Download and extract the MovieLens 25M dataset.

The zip is ~265 MB and extracts to ~1 GB. This is a one-shot step:
once the CSVs are under data/raw/ml-25m/ they should be versioned with
DVC, not re-downloaded. The script is idempotent — if the expected
files already exist it exits without touching the network.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import urllib.request
import zipfile
from pathlib import Path

from src.config import Settings

logger = logging.getLogger(__name__)

ML_25M_URL = "https://files.grouplens.org/datasets/movielens/ml-25m.zip"
ML_25M_DIRNAME = "ml-25m"

# The six CSVs the 25M release ships with. We don't currently ingest
# genome-* but they belong to the same logical dataset and should be
# fetched and DVC-tracked together so the cache is internally consistent.
EXPECTED_FILES: tuple[str, ...] = (
    "ratings.csv",
    "movies.csv",
    "tags.csv",
    "links.csv",
    "genome-scores.csv",
    "genome-tags.csv",
)


def download_movielens(dest_dir: Path, *, force: bool = False) -> Path:
    """Download and extract MovieLens 25M into ``dest_dir / ml-25m``.

    Returns the path to the extracted directory. With ``force=False`` (the
    default) the function is a no-op when all expected files already exist,
    which makes the script safe to run as a Make target.
    """
    extracted = dest_dir / ML_25M_DIRNAME
    if not force and _already_extracted(extracted):
        logger.info("MovieLens 25M already present at %s — skipping.", extracted)
        return extracted

    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "ml-25m.zip"

    logger.info("Downloading %s ...", ML_25M_URL)
    _stream_download(ML_25M_URL, zip_path)

    logger.info("Extracting to %s ...", dest_dir)
    if extracted.exists():
        shutil.rmtree(extracted)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    zip_path.unlink()  # Once extracted, DVC tracks the CSVs; the zip is dead weight.

    missing = [f for f in EXPECTED_FILES if not (extracted / f).exists()]
    if missing:
        raise RuntimeError(f"Extraction missing expected files: {missing}")
    logger.info("MovieLens 25M ready at %s", extracted)
    return extracted


def _already_extracted(extracted: Path) -> bool:
    return extracted.is_dir() and all((extracted / f).exists() for f in EXPECTED_FILES)


def _stream_download(url: str, dest: Path) -> None:
    """Stream to disk to keep memory flat on a ~265 MB file."""
    with urllib.request.urlopen(url) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download MovieLens 25M into the configured raw data dir.",
    )
    parser.add_argument("--force", action="store_true", help="Re-download even if files exist.")
    args = parser.parse_args()

    settings = Settings()
    download_movielens(settings.raw_data_dir, force=args.force)


if __name__ == "__main__":
    main()
