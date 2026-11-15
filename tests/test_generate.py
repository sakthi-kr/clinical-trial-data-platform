"""Phase-1 contracts for the deliberately clean synthetic study generator."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from clinical_trial.generate import generate_dataset
from clinical_trial.schema import TABLE_COLUMNS

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "study_protocol.json"


def read_rows(folder: Path, table: str) -> list[dict[str, str]]:
    with (folder / f"{table}.csv").open(encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))


def test_expected_counts_and_columns(tmp_path: Path):
    manifest = generate_dataset(CONFIG, tmp_path, participant_count=24, seed=101)
    expected = {"sites": 4, "participants": 24, "visits": 96, "vital_signs": 192,
                "laboratory_results": 384}
    for name, count in expected.items():
        assert manifest["tables"][name]["rows"] == count
        rows = read_rows(tmp_path, name)
        assert len(rows) == count
        with (tmp_path / f"{name}.csv").open(encoding="utf-8", newline="") as fp:
            assert tuple(csv.DictReader(fp).fieldnames or []) == TABLE_COLUMNS[name]
    assert manifest["synthetic_only"] is True


def test_no_broken_relationships_or_duplicate_ids(tmp_path: Path):
    generate_dataset(CONFIG, tmp_path, participant_count=60, seed=202)
    site_ids = {r["site_id"] for r in read_rows(tmp_path, "sites")}
    patients = read_rows(tmp_path, "participants")
    patient_ids = {r["participant_id"] for r in patients}
    assert len(patient_ids) == len(patients)
    assert all(p["site_id"] in site_ids for p in patients)

    visits = read_rows(tmp_path, "visits")
    visit_ids = {r["visit_id"] for r in visits}
    assert len(visit_ids) == len(visits)
    assert all(v["participant_id"] in patient_ids for v in visits)
    assert all(Counter(v["visit_code"] for v in visits if v["participant_id"] == p) ==
               Counter({"BASELINE": 1, "WEEK04": 1, "WEEK08": 1, "WEEK12": 1})
               for p in patient_ids)
    for table in ("vital_signs", "laboratory_results"):
        measurements = read_rows(tmp_path, table)
        assert len({r["measurement_id"] for r in measurements}) == len(measurements)
        assert all(r["visit_id"] in visit_ids for r in measurements)
        assert all(r["result_value"] and r["unit"] for r in measurements)
    assert all(a["participant_id"] in patient_ids for a in read_rows(tmp_path, "adverse_events"))


def test_visits_follow_protocol_windows(tmp_path: Path):
    generate_dataset(CONFIG, tmp_path, participant_count=40)
    expected = {"BASELINE": 0, "WEEK04": 28, "WEEK08": 56, "WEEK12": 84}
    enrollment = {r["participant_id"]: date.fromisoformat(r["enrollment_date"])
                  for r in read_rows(tmp_path, "participants")}
    for row in read_rows(tmp_path, "visits"):
        planned = date.fromisoformat(row["planned_date"])
        actual = date.fromisoformat(row["actual_date"])
        assert (planned - enrollment[row["participant_id"]]).days == expected[row["visit_code"]]
        assert abs((actual - planned).days) <= 3


def test_adverse_event_dates_are_consistent(tmp_path: Path):
    generate_dataset(CONFIG, tmp_path, participant_count=100, seed=7)
    enrollment = {r["participant_id"]: date.fromisoformat(r["enrollment_date"])
                  for r in read_rows(tmp_path, "participants")}
    for row in read_rows(tmp_path, "adverse_events"):
        start = date.fromisoformat(row["start_date"])
        assert enrollment[row["participant_id"]] <= start
        if row["end_date"]:
            assert start <= date.fromisoformat(row["end_date"])


def test_generation_is_byte_identical_for_same_seed(tmp_path: Path):
    folder_a, folder_b = tmp_path / "a", tmp_path / "b"
    a = generate_dataset(CONFIG, folder_a, participant_count=20, seed=333)
    b = generate_dataset(CONFIG, folder_b, participant_count=20, seed=333)
    assert a == b
    for name in TABLE_COLUMNS:
        assert (folder_a / f"{name}.csv").read_bytes() == (folder_b / f"{name}.csv").read_bytes()
    assert (folder_a / "manifest.json").read_bytes() == (folder_b / "manifest.json").read_bytes()


def test_new_seed_changes_data(tmp_path: Path):
    a = generate_dataset(CONFIG, tmp_path / "a", participant_count=12, seed=333)
    b = generate_dataset(CONFIG, tmp_path / "b", participant_count=12, seed=334)
    assert a["tables"]["participants"]["sha256"] != b["tables"]["participants"]["sha256"]


def test_invalid_size_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="positive"):
        generate_dataset(CONFIG, tmp_path, participant_count=0)


def test_no_personally_identifying_columns(tmp_path: Path):
    generate_dataset(CONFIG, tmp_path, participant_count=10)
    headers = {item.lower() for fields in TABLE_COLUMNS.values() for item in fields}
    assert not ({"first_name", "last_name", "address", "phone", "email", "birth_date"} & headers)


def test_manifest_integrity_verification(tmp_path: Path):
    from clinical_trial.verify import verify_manifest

    generate_dataset(CONFIG, tmp_path, participant_count=13)
    assert verify_manifest(tmp_path) == []


def test_manifest_verification_detects_modified_file(tmp_path: Path):
    from clinical_trial.verify import verify_manifest

    generate_dataset(CONFIG, tmp_path, participant_count=13)
    path = tmp_path / "participants.csv"
    path.write_text(path.read_text(encoding="utf-8") + "bad,row\n", encoding="utf-8")
    findings = verify_manifest(tmp_path)
    assert "SHA-256 mismatch: participants.csv" in findings
    assert "Row-count mismatch: participants.csv" in findings


def test_manifest_verification_detects_missing_file(tmp_path: Path):
    from clinical_trial.verify import verify_manifest

    generate_dataset(CONFIG, tmp_path, participant_count=13)
    (tmp_path / "visits.csv").unlink()
    assert "Missing CSV file: visits.csv" in verify_manifest(tmp_path)
