"""Unit and database integration tests for synthetic-data quality review."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from clinical_trial.generate import generate_dataset
from clinical_trial.quality import QualityError, _make_issue, load_rules

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "study_protocol.json"
RULES = ROOT / "configs" / "quality_rules.json"


def test_rule_config_load_and_hash():
    config, sha = load_rules(RULES)
    assert config["study_id"] == "SYN-CTQA-001"
    assert len(sha) == 64


def test_invalid_rules_rejected_before_database_access(tmp_path: Path):
    bad = json.loads(RULES.read_text(encoding="utf-8"))
    bad["visit_window_days"] = -1
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(QualityError, match="Invalid quality rules"):
        load_rules(file)


def test_issue_identity_stable_and_dataset_scoped():
    args = ("dataset-A", "M002", "ERROR", "vital_signs", "VS00001")
    a = _make_issue(*args, observed="kPa", expected="mmHg")
    b = _make_issue(*args, observed="kPa", expected="mmHg")
    c = _make_issue("dataset-B", *args[1:], observed="kPa", expected="mmHg")
    assert a == b
    assert a["issue_id"] != c["issue_id"]


duckdb = pytest.importorskip("duckdb")
from clinical_trial.ingest import load_dataset
from clinical_trial.quality import evaluate_dataset, current_issues, write_report


def _db(tmp_path: Path, n=40, seed=31, folder="study") -> tuple[Path, str]:
    source = tmp_path / folder
    generate_dataset(CONFIG, source, participant_count=n, seed=seed)
    db = tmp_path / "study.duckdb"
    result = load_dataset(source, db)
    assert result["status"] == "LOADED"
    return db, result["dataset_id"]


def _execute(db: Path, sql: str, params: list | None = None):
    con = duckdb.connect(str(db))
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def test_clean_generated_dataset_has_no_findings(tmp_path: Path):
    db, dataset_id = _db(tmp_path, n=75)
    outcome = evaluate_dataset(db, RULES)
    assert outcome["issue_count"] == 0
    assert outcome["rule_count"] >= 15
    assert outcome["by_rule"] == {}
    assert current_issues(db, dataset_id) == []
    assert _execute(db, "SELECT issue_count FROM quality_runs") == [(0,)]
    report = tmp_path / "issues.csv"
    write_report(db, dataset_id, report)
    with report.open(newline="", encoding="utf-8") as stream:
        assert list(csv.DictReader(stream)) == []


def test_out_of_range_value_unit_and_visit_window(tmp_path: Path):
    db, dataset_id = _db(tmp_path, n=30)
    _execute(db, "UPDATE participants SET age_years=97 WHERE participant_id='P000001'")
    _execute(db, "UPDATE visits SET actual_date=actual_date+INTERVAL 30 DAY WHERE visit_id='V0000002'")
    _execute(db, "UPDATE vital_signs SET unit='kPa', result_value=999 WHERE measurement_id='VS00000001'")
    result = evaluate_dataset(db, RULES)
    rules = {x["rule_id"] for x in current_issues(db, dataset_id)}
    assert {"P001", "V005", "M002", "M003"} <= rules
    assert result["issue_count"] == len(current_issues(db, dataset_id))
    assert _execute(db, "SELECT age_years FROM participants WHERE participant_id='P000001'") == [(97,)]


def test_missing_and_duplicate_visits_and_measurements(tmp_path: Path):
    db, dataset_id = _db(tmp_path)
    # Remove every child of the baseline visit before removing the visit.
    _execute(db, "DELETE FROM laboratory_results WHERE visit_id='V0000001'")
    _execute(db, "DELETE FROM vital_signs WHERE visit_id='V0000001'")
    _execute(db, "DELETE FROM visits WHERE visit_id='V0000001'")
    # Duplicate a visit code for participant 2 with a fresh identifier.
    _execute(db, """INSERT INTO visits
            SELECT dataset_id, 'V9999999', participant_id, visit_code,
                   planned_date, actual_date FROM visits WHERE visit_id='V0000005'""")
    # Remove one expected lab from participant 2, and duplicate one vital.
    _execute(db, "DELETE FROM laboratory_results WHERE measurement_id='LB00000005'")
    _execute(db, """INSERT INTO vital_signs
            SELECT dataset_id, 'VS99999999', visit_id, test_code, result_value, unit
            FROM vital_signs WHERE measurement_id='VS00000009'""")
    evaluate_dataset(db, RULES)
    issues = current_issues(db, dataset_id)
    rule_ids = {issue["rule_id"] for issue in issues}
    assert {"V001", "V003", "M004", "M005"} <= rule_ids
    assert any(issue["record_id"] == 'P000001/BASELINE' for issue in issues)
    assert any(issue["record_id"] == 'V0000002/HGB' for issue in issues)  # removed laboratory value


def test_abnormal_adverse_event_dates_and_codes(tmp_path: Path):
    db, dataset_id = _db(tmp_path, n=120)
    aid, start = _execute(db, "SELECT event_id, start_date FROM adverse_events LIMIT 1")[0]
    _execute(db, "UPDATE adverse_events SET end_date=start_date-INTERVAL 1 DAY, "
                 "severity='UNKNOWN', event_term='OTHER' WHERE event_id=?", [aid])
    evaluate_dataset(db, RULES)
    rules = {x["rule_id"] for x in current_issues(db, dataset_id)}
    assert {"A001", "A003", "A004"} <= rules


def test_repeat_evaluations_replace_findings_and_retain_history(tmp_path: Path):
    db, dataset_id = _db(tmp_path)
    _execute(db, "UPDATE participants SET study_group='UNKNOWN' WHERE participant_id='P000001'")
    first = evaluate_dataset(db, RULES)
    a = current_issues(db, dataset_id)
    second = evaluate_dataset(db, RULES)
    b = current_issues(db, dataset_id)
    assert first["issue_count"] == 1
    assert second["issue_count"] == 1
    assert first["run_id"] != second["run_id"]
    assert a == b
    assert _execute(db, "SELECT COUNT(*) FROM quality_runs")[0][0] == 2
    _execute(db, "UPDATE participants SET study_group='ARM_A' WHERE participant_id='P000001'")
    result = evaluate_dataset(db, RULES)
    assert result["issue_count"] == 0
    assert current_issues(db, dataset_id) == []
    assert _execute(db, "SELECT COUNT(*) FROM quality_runs")[0][0] == 3


def test_two_datasets_must_be_selected_and_remain_isolated(tmp_path: Path):
    db, a_id = _db(tmp_path, seed=17, folder='a')
    source_b = tmp_path / 'b'
    generate_dataset(CONFIG, source_b, participant_count=40, seed=18)
    b_id = load_dataset(source_b, db)["dataset_id"]
    with pytest.raises(QualityError, match="More than one dataset"):
        evaluate_dataset(db, RULES)
    _execute(db, "UPDATE participants SET age_years=99 WHERE dataset_id=? AND participant_id='P000001'", [a_id])
    r_a = evaluate_dataset(db, RULES, dataset_id=a_id)
    r_b = evaluate_dataset(db, RULES, dataset_id=b_id)
    assert r_a["issue_count"] == 1
    assert r_b["issue_count"] == 0
    assert len(current_issues(db, a_id)) == 1
    assert current_issues(db, b_id) == []


def test_rule_study_mismatch_does_not_write_history(tmp_path: Path):
    db, dataset_id = _db(tmp_path)
    config = json.loads(RULES.read_text(encoding="utf-8"))
    config["study_id"] = "WRONG"
    path = tmp_path / "wrong_rules.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(QualityError, match="rules target study".replace('rules', 'Rules')):
        evaluate_dataset(db, path)
    assert _execute(db, "SELECT COUNT(*) FROM quality_runs")[0][0] == 0


def test_csv_report_contains_issue_metadata(tmp_path: Path):
    db, dataset_id = _db(tmp_path)
    _execute(db, "UPDATE vital_signs SET unit='unknown' WHERE measurement_id='VS00000001'")
    evaluate_dataset(db, RULES)
    report = tmp_path / 'out' / 'issues.csv'
    write_report(db, dataset_id, report)
    with report.open(newline="", encoding="utf-8") as stream:
        issues = list(csv.DictReader(stream))
    assert len(issues) == 1
    assert issues[0]["rule_id"] == "M002"
    assert issues[0]["record_id"] == "VS00000001"
    assert issues[0]["expected_value"] == 'mmHg'
