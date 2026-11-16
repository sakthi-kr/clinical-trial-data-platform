"""Dataset-scoped dashboard queries and filters, independent of the UI framework."""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from clinical_trial.dashboard_data import (
    DashboardDataError, EXPORT_COLUMNS, count_by, filter_issues, issue_csv,
    list_datasets, load_snapshot,
)
from clinical_trial.generate import generate_dataset
from clinical_trial.ingest import load_dataset
from clinical_trial.quality import evaluate_dataset

pytest.importorskip("duckdb")
import duckdb

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "configs/study_protocol.json"
RULES = ROOT / "configs/quality_rules.json"


def _study(tmp_path: Path, seed: int = 17, db: Path | None = None):
    src = tmp_path / f"source_{seed}"
    generate_dataset(STUDY, src, participant_count=30, seed=seed)
    db = db or (tmp_path / "study.duckdb")
    dataset_id = load_dataset(src, db)["dataset_id"]
    return db, dataset_id


def _mutate(db: Path, sql: str, *args):
    con = duckdb.connect(str(db))
    try:
        con.execute(sql, list(args))
    finally:
        con.close()


def test_empty_or_missing_database_is_reported(tmp_path: Path):
    with pytest.raises(DashboardDataError, match="not found"):
        list_datasets(tmp_path / "absent.duckdb")


def test_list_and_snapshot_are_dataset_scoped(tmp_path: Path):
    db, a = _study(tmp_path, seed=7)
    _, b = _study(tmp_path, seed=8, db=db)
    assert {x["dataset_id"] for x in list_datasets(db)} == {a, b}
    _mutate(db, "UPDATE participants SET age_years=91 WHERE dataset_id=? AND participant_id='P000001'", a)
    evaluate_dataset(db, RULES, dataset_id=a)
    evaluate_dataset(db, RULES, dataset_id=b)
    first = load_snapshot(db, a)
    second = load_snapshot(db, b)
    assert first["counts"]["participants"] == second["counts"]["participants"] == 30
    assert len(first["issues"]) == 1
    assert first["issues"][0]["rule_id"] == "P001"
    assert not second["issues"]
    assert len(first["history"]) == len(second["history"]) == 1


def test_snapshot_has_real_site_and_visit_for_measurement(tmp_path: Path):
    db, dataset = _study(tmp_path)
    _mutate(db, "UPDATE vital_signs SET unit='kPa' WHERE measurement_id='VS00000001'")
    evaluate_dataset(db, RULES)
    snap = load_snapshot(db, dataset)
    assert len(snap["issues"]) == 1
    row = snap["issues"][0]
    assert row["site_id"] == "S01"
    assert row["visit_code"] == "BASELINE"
    assert row["participant_id"] == "P000001"
    assert snap["history"][0]["issue_count"] == 1


def test_missing_visit_rule_resolves_expected_visit_code(tmp_path: Path):
    db, dataset = _study(tmp_path)
    _mutate(db, "DELETE FROM laboratory_results WHERE visit_id='V0000001'")
    _mutate(db, "DELETE FROM vital_signs WHERE visit_id='V0000001'")
    _mutate(db, "DELETE FROM visits WHERE visit_id='V0000001'")
    evaluate_dataset(db, RULES)
    issues = load_snapshot(db, dataset)["issues"]
    missing = [i for i in issues if i["rule_id"] == "V001"]
    assert len(missing) == 1
    assert missing[0]["site_id"] == "S01"
    assert missing[0]["visit_code"] == "BASELINE"


def test_combined_filters_aggregation_and_csv_export(tmp_path: Path):
    db, dataset = _study(tmp_path)
    _mutate(db, "UPDATE participants SET age_years=99 WHERE participant_id='P000001'")
    _mutate(db, "UPDATE participants SET age_years=99 WHERE participant_id='P000002'")
    _mutate(db, "UPDATE vital_signs SET unit='kPa' WHERE measurement_id='VS00000001'")
    evaluate_dataset(db, RULES)
    issues = load_snapshot(db, dataset)["issues"]
    assert len(issues) == 3
    result = filter_issues(issues, site_id="S01", visit_code="BASELINE", severity="ERROR",
                           rule_ids=["M002"], participant_text="000001")
    assert len(result) == 1
    assert result[0]["rule_id"] == "M002"
    assert count_by(issues, "rule_id") == [
        {"name": "P001", "count": 2}, {"name": "M002", "count": 1}
    ]
    assert count_by(result, "site_id") == [{"name": "S01", "count": 1}]
    exported = list(csv.DictReader(io.StringIO(issue_csv(result).decode("utf-8-sig"))))
    assert len(exported) == 1
    assert tuple(exported[0]) == EXPORT_COLUMNS
    assert exported[0]["rule_id"] == "M002"
    assert "result_value" not in exported[0]


def test_no_changes_from_dashboard_readers(tmp_path: Path):
    db, dataset = _study(tmp_path)
    evaluate_dataset(db, RULES)
    before = db.read_bytes()
    list_datasets(db)
    load_snapshot(db, dataset)
    assert db.read_bytes() == before


def test_unknown_dataset_and_unsupported_aggregation(tmp_path: Path):
    db, _ = _study(tmp_path)
    with pytest.raises(DashboardDataError, match="not present"):
        load_snapshot(db, "fake")
    with pytest.raises(ValueError, match="Unsupported"):
        count_by([], "participant_id")


def test_unreviewed_dataset_is_not_misrepresented_as_clean(tmp_path: Path):
    db, dataset = _study(tmp_path)
    snap = load_snapshot(db, dataset)
    assert snap["history"] == []
    assert snap["issues"] == []
