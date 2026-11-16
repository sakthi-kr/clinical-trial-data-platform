"""Synthetic fault-injection accuracy and reproducibility tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from clinical_trial.challenge import ChallengeError, _metrics, inject_fixture, score_fixture
from clinical_trial.generate import generate_dataset
from clinical_trial.validate_source import check_source

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/study_protocol.json"
RULES = ROOT / "configs/quality_rules.json"


def _source(tmp_path: Path, n: int = 180) -> Path:
    clean = tmp_path / "clean"
    generate_dataset(PROTOCOL, clean, participant_count=n)
    return clean


def test_injected_csv_is_structurally_valid_and_does_not_modify_source(tmp_path: Path):
    clean = _source(tmp_path)
    original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in clean.iterdir()}
    result = inject_fixture(clean, tmp_path / "challenge", RULES)
    assert result["case_count"] == 19
    assert result["expected_findings"] == 22
    assert result["rules_covered"] == 19
    assert check_source(tmp_path / "challenge")["synthetic_only"] is True
    assert original == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in clean.iterdir()}
    truth = json.loads((tmp_path / "challenge/ground_truth.json").read_text())
    assert truth["fixture_manifest_sha256"] == result["dataset_id"]
    assert {f["rule_id"] for c in truth["cases"] for f in c["expected_findings"]} == {
        "P001", "P002", "P003", "V001", "V002", "V003", "V004", "V005", "V006",
        "M001", "M002", "M003", "M004", "M005", "A001", "A002", "A003", "A004", "A005",
    }


def test_repeat_challenge_creation_is_identical_across_output_directories(tmp_path: Path):
    clean = _source(tmp_path)
    first = inject_fixture(clean, tmp_path / "first", RULES)
    second = inject_fixture(clean, tmp_path / "second", RULES)
    assert first["dataset_id"] == second["dataset_id"]
    for name in ["manifest.json", "ground_truth.json", "visits.csv", "adverse_events.csv"]:
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_does_not_overwrite_existing_fixture_or_baseline(tmp_path: Path):
    clean = _source(tmp_path)
    with pytest.raises(ChallengeError, match="separate"):
        inject_fixture(clean, clean, RULES)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("leave me alone")
    with pytest.raises(ChallengeError, match="already exists"):
        inject_fixture(clean, existing, RULES)
    assert (existing / "keep.txt").read_text() == "leave me alone"


def test_too_small_source_rejected_without_creating_output(tmp_path: Path):
    clean = _source(tmp_path, n=10)
    with pytest.raises(ChallengeError, match="at least 18"):
        inject_fixture(clean, tmp_path / "challenge", RULES)
    assert not (tmp_path / "challenge").exists()


def test_exact_match_metrics_include_false_positives_and_false_negatives():
    expected = {("P001", "participants", "P1"), ("M002", "vital_signs", "M1")}
    found = {("P001", "participants", "P1"), ("P001", "participants", "P2")}
    m = _metrics(expected, found)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
    assert (m["precision"], m["recall"], m["f1"]) == (0.5, 0.5, 0.5)


duckdb = pytest.importorskip("duckdb")
from clinical_trial.ingest import load_dataset


def _loaded(tmp_path: Path) -> tuple[Path, Path]:
    clean = _source(tmp_path)
    out = tmp_path / "challenge"
    inject_fixture(clean, out, RULES)
    db = tmp_path / "challenge.duckdb"
    # Include the clean dataset to verify dataset-scoped scoring.
    load_dataset(clean, db)
    load_dataset(out, db)
    return db, out / "ground_truth.json"


def _execute(db: Path, sql: str, args: list | None = None) -> None:
    con = duckdb.connect(str(db))
    try:
        con.execute(sql, args or [])
    finally:
        con.close()


def test_all_injected_findings_are_detected_without_extras(tmp_path: Path):
    db, truth = _loaded(tmp_path)
    result = score_fixture(db, truth, RULES, tmp_path / "report")
    assert result["expected_findings"] == 22
    assert result["detected_findings"] == 22
    assert result["overall"]["tp"] == 22
    assert result["overall"]["fp"] == 0
    assert result["overall"]["fn"] == 0
    assert result["overall"]["precision"] == 1.0
    assert result["overall"]["recall"] == 1.0
    assert all(v["tp"] > 0 for v in result["by_rule"].values())
    assert all(x["passed"] for x in result["cases"])
    assert (tmp_path / "report/benchmark.json").exists()
    assert (tmp_path / "report/detected_issues.csv").exists()
    # An evaluation should be repeatable; issue identities remain stable.
    again = score_fixture(db, truth, RULES)
    assert again["overall"] == result["overall"]


def test_extra_issue_is_counted_as_false_positive_against_fixture(tmp_path: Path):
    db, truth = _loaded(tmp_path)
    fixture_id = json.loads(truth.read_text())["fixture_manifest_sha256"]
    _execute(db, "UPDATE participants SET age_years=99 WHERE dataset_id=? AND participant_id='P000020'", [fixture_id])
    result = score_fixture(db, truth, RULES)
    assert result["overall"]["tp"] == 22
    assert result["overall"]["fp"] == 1
    assert result["overall"]["fn"] == 0
    assert result["false_positives"][0]["record_id"] == "P000020"


def test_repaired_injected_record_counts_as_false_negative(tmp_path: Path):
    db, truth = _loaded(tmp_path)
    fixture_id = json.loads(truth.read_text())["fixture_manifest_sha256"]
    _execute(db, "UPDATE participants SET age_years=50 WHERE dataset_id=? AND participant_id='P000015'", [fixture_id])
    result = score_fixture(db, truth, RULES)
    assert result["overall"]["tp"] == 21
    assert result["overall"]["fn"] == 1
    assert result["overall"]["fp"] == 0
    assert result["false_negatives"][0]["rule_id"] == "P001"


def test_scoring_rejects_changed_rules_before_evaluation(tmp_path: Path):
    db, truth = _loaded(tmp_path)
    rules = json.loads(RULES.read_text())
    rules["visit_window_days"] += 1
    modified = tmp_path / "altered_rules.json"
    modified.write_text(json.dumps(rules))
    with pytest.raises(ChallengeError, match="differs"):
        score_fixture(db, truth, modified)
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM quality_runs").fetchone()[0] == 0
    finally:
        con.close()
