from pathlib import Path

from src.data.download import EXPECTED_FILES, ML_25M_DIRNAME, ML_25M_URL, _already_extracted


def test_url_points_at_grouplens() -> None:
    # Guard against an accidental edit to the URL constant.
    assert ML_25M_URL.startswith("https://")
    assert "grouplens.org" in ML_25M_URL
    assert ML_25M_URL.endswith("ml-25m.zip")


def test_already_extracted_true_when_all_files_present(tmp_path: Path) -> None:
    extracted = tmp_path / ML_25M_DIRNAME
    extracted.mkdir()
    for name in EXPECTED_FILES:
        (extracted / name).touch()
    assert _already_extracted(extracted) is True


def test_already_extracted_false_when_one_file_missing(tmp_path: Path) -> None:
    extracted = tmp_path / ML_25M_DIRNAME
    extracted.mkdir()
    for name in EXPECTED_FILES[:-1]:
        (extracted / name).touch()
    assert _already_extracted(extracted) is False


def test_already_extracted_false_when_dir_absent(tmp_path: Path) -> None:
    assert _already_extracted(tmp_path / "does-not-exist") is False
