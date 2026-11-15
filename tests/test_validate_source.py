"""Tests runnable without DuckDB: preflight checks for source contracts."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from clinical_trial.generate import generate_dataset
from clinical_trial.validate_source import SourceValidationError, check_source

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "study_protocol.json"


def change_cell(folder: Path, table: str, column: str, new_value: str, row_index: int = 0) -> None:
    """Deliberately modify a source and update the manifest so hash checks pass."""
    file = folder / f"{table}.csv"
    with file.open(encoding="utf-8", newline="") as stream:
        data = list(csv.DictReader(stream))
    data[row_index][column] = new_value
    with file.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(data)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["tables"][table]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def seed_dataset(tmp_path: Path) -> Path:
    folder = tmp_path / "source"
    generate_dataset(CONFIG, folder, participant_count=15, seed=77)
    return folder


def test_clean_source_passes(tmp_path: Path):
    folder = seed_dataset(tmp_path)
    result = check_source(folder)
    assert result["synthetic_only"] is True
    assert result["tables"]["participants"]["rows"] == 15


def test_manifest_checksum_required(tmp_path: Path):
    folder = seed_dataset(tmp_path)
    (folder / "visits.csv").write_bytes((folder / "visits.csv").read_bytes() + b"junk\n")
    with pytest.raises(SourceValidationError, match="SHA-256 mismatch"):
        check_source(folder)


@pytest.mark.parametrize("table,column,value,error", [
    ("participants", "age_years", "not a number", "invalid integer"),
    ("visits", "planned_date", "2026-13-01", "invalid ISO date"),
    ("vital_signs", "result_value", "inf", "invalid numeric"),
    ("laboratory_results", "result_value", "nan", "invalid numeric"),
    ("visits", "participant_id", "P999999", "unknown participants"),
    ("participants", "site_id", "S999", "unknown sites"),
    ("adverse_events", "event_id", "", "missing event_id"),
])
def test_invalid_values_rejected(tmp_path: Path, table: str, column: str, value: str, error: str):
    folder = seed_dataset(tmp_path)
    # AE may contain no events for small seed, so ensure it has a row.
    if table == "adverse_events":
        generate_dataset(CONFIG, folder, participant_count=100, seed=77)
    change_cell(folder, table, column, value)
    with pytest.raises(SourceValidationError, match=error):
        check_source(folder)


def test_duplicate_primary_key_rejected(tmp_path: Path):
    folder = seed_dataset(tmp_path)
    with (folder / "participants.csv").open(newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    change_cell(folder, "participants", "participant_id", rows[0]["participant_id"], row_index=1)
    with pytest.raises(SourceValidationError, match="duplicate participant_id"):
        check_source(folder)


def test_ongoing_adverse_event_null_end_date_allowed(tmp_path: Path):
    folder = tmp_path / "source"
    generate_dataset(CONFIG, folder, participant_count=120, seed=5)
    change_cell(folder, "adverse_events", "end_date", "")
    assert check_source(folder)["synthetic_only"] is True


def test_preflight_does_not_enforce_clinical_ranges_yet(tmp_path: Path):
    folder = seed_dataset(tmp_path)
    # Clinical-range checks are separate from structural preflight.
    change_cell(folder, "laboratory_results", "result_value", "100000.3")
    assert check_source(folder)["synthetic_only"] is True
