"""Synthetic issue review lifecycle and append-only metadata tests."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("duckdb")
import duckdb

from clinical_trial.generate import generate_dataset
from clinical_trial.ingest import load_dataset
from clinical_trial.quality import evaluate_dataset, current_issues
from clinical_trial.dashboard_data import load_snapshot
from clinical_trial.review import ReviewError, finding_signature, record_review

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/study_protocol.json"
RULES = ROOT / "configs/quality_rules.json"


def study(tmp_path, seed=31, db=None):
    source = tmp_path / f"source_{seed}"
    generate_dataset(PROTOCOL, source, participant_count=20, seed=seed)
    db = db or tmp_path / "study.duckdb"
    dataset = load_dataset(source, db)["dataset_id"]
    return db, dataset


def mutate(db, query, *args):
    con = duckdb.connect(str(db))
    try:
        con.execute(query, list(args))
    finally:
        con.close()


def introduce_age_issue(db, dataset):
    mutate(db, "UPDATE participants SET age_years=98 WHERE dataset_id=? AND participant_id='P000001'", dataset)
    evaluate_dataset(db, RULES, dataset_id=dataset)
    issue = next(x for x in current_issues(db, dataset) if x["rule_id"] == "P001")
    return issue


def test_fingerprint_changes_with_observed_value():
    a = dict(rule_id="P001", severity="WARNING", source_table="participants", record_id="P000001",
             observed_value="98", expected_value="18..80")
    b = dict(a, observed_value="99")
    assert finding_signature(a) != finding_signature(b)
    assert finding_signature(a) == finding_signature(dict(a))


def test_review_action_keeps_study_data_and_appends_history(tmp_path):
    db, dataset = study(tmp_path)
    issue = introduce_age_issue(db, dataset)
    initial = load_snapshot(db, dataset)["issues"][0]
    assert initial["review_status"] == "UNREVIEWED"
    assert initial["review_version"] == 0
    outcome = record_review(db, dataset_id=dataset, issue_id=issue["issue_id"],
                            status="IN_REVIEW", reviewer="demo analyst", note="Checking the synthetic age record",
                            expected_version=0)
    assert outcome["review_version"] == 1
    second = record_review(db, dataset_id=dataset, issue_id=issue["issue_id"],
                           status="CONFIRMED", reviewer="demo analyst", note="Synthetic protocol flag confirmed",
                           expected_version=1)
    assert second["review_version"] == 2
    snap = load_snapshot(db, dataset)
    assert snap["issues"][0]["review_status"] == "CONFIRMED"
    assert snap["issues"][0]["review_version"] == 2
    assert len(snap["review_history"]) == 2
    assert snap["review_history"][0]["status"] == "IN_REVIEW"
    assert snap["review_history"][1]["status"] == "CONFIRMED"
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT age_years FROM participants WHERE dataset_id=? AND participant_id='P000001'", [dataset]).fetchone()[0] == 98
    finally:
        con.close()


def test_invalid_and_stale_actions_do_not_insert(tmp_path):
    db, dataset = study(tmp_path)
    issue = introduce_age_issue(db, dataset)
    kwargs = dict(dataset_id=dataset, issue_id=issue["issue_id"], status="IN_REVIEW", reviewer="reviewer 1",
                  note="Checking synthetic observation", expected_version=0)
    for changes in [dict(status="ACCEPTED"), dict(reviewer=""), dict(note=""), dict(expected_version=-1),
                    dict(note="A" * 1001), dict(dataset_id="unknown")]:
        with pytest.raises(ReviewError):
            record_review(db, **(kwargs | changes))
    assert load_snapshot(db, dataset)["review_history"] == []
    record_review(db, **kwargs)
    with pytest.raises(ReviewError, match="changed in another session"):
        record_review(db, **kwargs)
    assert len(load_snapshot(db, dataset)["review_history"]) == 1


def test_content_changes_reset_review_without_erasing_audit(tmp_path):
    db, dataset = study(tmp_path)
    issue = introduce_age_issue(db, dataset)
    record_review(db, dataset_id=dataset, issue_id=issue["issue_id"],
                  status="CONFIRMED", reviewer="reviewer 1", note="Synthetic finding recorded", expected_version=0)
    # Re-evaluation of an unchanged finding preserves the same review.
    evaluate_dataset(db, RULES, dataset_id=dataset)
    assert load_snapshot(db, dataset)["issues"][0]["review_status"] == "CONFIRMED"
    # Same rule/record but a different observed value must NOT reuse the review.
    mutate(db, "UPDATE participants SET age_years=99 WHERE dataset_id=? AND participant_id='P000001'", dataset)
    evaluate_dataset(db, RULES, dataset_id=dataset)
    snap = load_snapshot(db, dataset)
    assert snap["issues"][0]["issue_id"] == issue["issue_id"]
    assert snap["issues"][0]["review_status"] == "UNREVIEWED"
    assert len(snap["review_history"]) == 1
    # Disappearance removes it from current issues but history remains.
    mutate(db, "UPDATE participants SET age_years=50 WHERE dataset_id=? AND participant_id='P000001'", dataset)
    evaluate_dataset(db, RULES, dataset_id=dataset)
    snap = load_snapshot(db, dataset)
    assert snap["issues"] == []
    assert len(snap["review_history"]) == 1


def test_review_events_remain_dataset_isolated(tmp_path):
    db, a = study(tmp_path, seed=31)
    _, b = study(tmp_path, seed=32, db=db)
    issue = introduce_age_issue(db, a)
    record_review(db, dataset_id=a, issue_id=issue["issue_id"], status="IN_REVIEW",
                  reviewer="analyst", note="Check generated participant", expected_version=0)
    evaluate_dataset(db, RULES, dataset_id=b)
    assert len(load_snapshot(db, a)["review_history"]) == 1
    assert len(load_snapshot(db, b)["review_history"]) == 0
    with pytest.raises(ReviewError, match="not found"):
        record_review(db, dataset_id=b, issue_id=issue["issue_id"], status="IN_REVIEW",
                      reviewer="analyst", note="Wrong dataset attempt", expected_version=0)
