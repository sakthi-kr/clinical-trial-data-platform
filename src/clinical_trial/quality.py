"""Reproducible, non-destructive quality review for synthetic study data.

Structural rejection happens before ingestion. This module flags values that
are structurally valid but inconsistent with a configurable fictional protocol.
It does not make clinical judgements or modify underlying observations.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from clinical_trial.ingest import _db_module, initialize_schema


class QualityError(ValueError):
    """A quality evaluation cannot be completed."""


# Stable, documented rule identifiers. A finding has one rule and one record.
RULES = {
    "P001": "Age outside fictional study eligibility bounds",
    "P002": "Unknown treatment arm",
    "P003": "Unknown recorded sex code",
    "V001": "Expected visit missing",
    "V002": "Unrecognized visit code",
    "V003": "Duplicate visit code for participant",
    "V004": "Planned visit date differs from schedule",
    "V005": "Actual visit date outside protocol window",
    "V006": "Visit occurred before enrolment",
    "M001": "Unrecognized measurement code",
    "M002": "Measurement unit does not match protocol",
    "M003": "Measurement outside configured review bounds",
    "M004": "Expected measurement missing",
    "M005": "Duplicate measurement code for visit",
    "A001": "Adverse-event end date precedes start date",
    "A002": "Adverse event starts before enrolment",
    "A003": "Unexpected adverse-event severity",
    "A004": "Unknown adverse-event term",
    "A005": "Adverse event starts after study follow-up window",
}

TABLES = {
    "participants": "participant_id",
    "visits": "visit_id",
    "vital_signs": "measurement_id",
    "laboratory_results": "measurement_id",
    "adverse_events": "event_id",
}

REPORT_FIELDS = (
    "dataset_id", "issue_id", "rule_id", "severity", "source_table", "record_id",
    "participant_id", "visit_id", "message", "observed_value", "expected_value"
)


def _validate_rules(config: dict[str, Any]) -> None:
    """Reject incomplete or inconsistent rule definitions before database writes."""
    try:
        if not isinstance(config["study_id"], str) or not config["study_id"].strip():
            raise ValueError("study_id must be nonempty")
        if len(set(config["allowed_groups"])) != len(config["allowed_groups"]):
            raise ValueError("duplicate study groups")
        for key in ("allowed_groups", "allowed_recorded_sex", "allowed_adverse_event_terms", "allowed_adverse_event_severity"):
            if not config[key] or not all(isinstance(x, str) and x for x in config[key]):
                raise ValueError(f"invalid {key}")
        age = config["age_years"]
        if not isinstance(age["min"], int) or not isinstance(age["max"], int) or age["min"] > age["max"]:
            raise ValueError("invalid age bounds")
        days = [v["day"] for v in config["visits"]]
        codes = [v["visit_code"] for v in config["visits"]]
        if not days or any(not isinstance(d, int) or d < 0 for d in days):
            raise ValueError("invalid visit days")
        if days[0] != 0 or sorted(set(days)) != days or len(set(codes)) != len(codes):
            raise ValueError("expected visits must have unique increasing days starting at zero")
        if not all(isinstance(c, str) and c for c in codes):
            raise ValueError("invalid visit code")
        if not isinstance(config["visit_window_days"], int) or config["visit_window_days"] < 0:
            raise ValueError("invalid visit window")
        for table in ("vital_signs", "laboratory_results"):
            if not config[table]:
                raise ValueError(f"empty {table} rules")
            for code, spec in config[table].items():
                if not code or not isinstance(spec["unit"], str) or not spec["unit"]:
                    raise ValueError(f"invalid {table}.{code} unit")
                low, high = Decimal(str(spec["review_min"])), Decimal(str(spec["review_max"]))
                if not low.is_finite() or not high.is_finite() or low > high:
                    raise ValueError(f"invalid {table}.{code} review bounds")
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise QualityError(f"Invalid quality rules: {exc}") from exc


def load_rules(path: Path) -> tuple[dict[str, Any], str]:
    """Read and hash the exact rule configuration for reproducibility."""
    path = Path(path)
    try:
        payload = path.read_bytes()
        config = json.loads(payload)
        if not isinstance(config, dict):
            raise ValueError("rules must be a JSON object")
        _validate_rules(config)
        return config, hashlib.sha256(payload).hexdigest()
    except (OSError, UnicodeError, ValueError) as exc:
        raise QualityError(f"Cannot read quality rules from {path}: {exc}") from exc


def _rows(con: Any, table: str, dataset_id: str) -> list[dict[str, Any]]:
    # Table names come only from the fixed, module-owned whitelist.
    assert table in TABLES
    result = con.execute(
        f"SELECT * FROM {table} WHERE dataset_id = ? ORDER BY {TABLES[table]}",
        [dataset_id],
    )
    names = [col[0] for col in result.description]
    return [dict(zip(names, record)) for record in result.fetchall()]


def _make_issue(
    dataset_id: str, rule_id: str, severity: str, table: str, record_id: str,
    *, participant_id: str | None = None, visit_id: str | None = None,
    observed: Any = None, expected: Any = None,
) -> dict[str, Any]:
    if rule_id not in RULES or severity not in ("WARNING", "ERROR"):
        raise QualityError("Unknown rule or severity")
    identifier = f"{dataset_id}|{rule_id}|{table}|{record_id}"
    return {
        "dataset_id": dataset_id,
        "issue_id": hashlib.sha256(identifier.encode("utf-8")).hexdigest(),
        "rule_id": rule_id,
        "severity": severity,
        "source_table": table,
        "record_id": record_id,
        "participant_id": participant_id,
        "visit_id": visit_id,
        "message": RULES[rule_id],
        "observed_value": None if observed is None else str(observed),
        "expected_value": None if expected is None else str(expected),
    }


def inspect_dataset(con: Any, dataset_id: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Calculate issues using read-only source data; no data is corrected."""
    _validate_rules(config)
    participants = _rows(con, "participants", dataset_id)
    visits = _rows(con, "visits", dataset_id)
    by_participant = {p["participant_id"]: p for p in participants}
    by_visit = {v["visit_id"]: v for v in visits}
    expected_visits = {v["visit_code"]: int(v["day"]) for v in config["visits"]}
    window = timedelta(days=config["visit_window_days"])
    issue_map: dict[str, dict[str, Any]] = {}

    def add(rule_id: str, severity: str, table: str, record_id: str, **fields: Any) -> None:
        issue = _make_issue(dataset_id, rule_id, severity, table, record_id, **fields)
        if issue["issue_id"] in issue_map:
            raise QualityError(f"Duplicate issue identity for {rule_id} {record_id}")
        issue_map[issue["issue_id"]] = issue

    for p in participants:
        pid = p["participant_id"]
        age = p["age_years"]
        if not config["age_years"]["min"] <= age <= config["age_years"]["max"]:
            add("P001", "WARNING", "participants", pid, participant_id=pid,
                observed=age, expected=f'{config["age_years"]["min"]}..{config["age_years"]["max"]}')
        if p["study_group"] not in config["allowed_groups"]:
            add("P002", "ERROR", "participants", pid, participant_id=pid,
                observed=p["study_group"], expected=", ".join(config["allowed_groups"]))
        if p["recorded_sex"] not in config["allowed_recorded_sex"]:
            add("P003", "WARNING", "participants", pid, participant_id=pid,
                observed=p["recorded_sex"], expected=", ".join(config["allowed_recorded_sex"]))

    visits_by_pid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in visits:
        visits_by_pid[v["participant_id"]].append(v)
        pid, vid, code = v["participant_id"], v["visit_id"], v["visit_code"]
        p = by_participant[pid]
        if code not in expected_visits:
            add("V002", "ERROR", "visits", vid, participant_id=pid, visit_id=vid,
                observed=code, expected=", ".join(expected_visits))
        else:
            scheduled = p["enrollment_date"] + timedelta(days=expected_visits[code])
            if v["planned_date"] != scheduled:
                add("V004", "ERROR", "visits", vid, participant_id=pid, visit_id=vid,
                    observed=v["planned_date"], expected=scheduled)
            if not scheduled - window <= v["actual_date"] <= scheduled + window:
                add("V005", "WARNING", "visits", vid, participant_id=pid, visit_id=vid,
                    observed=v["actual_date"], expected=f"{scheduled - window}..{scheduled + window}")
        if v["actual_date"] < p["enrollment_date"]:
            add("V006", "ERROR", "visits", vid, participant_id=pid, visit_id=vid,
                observed=v["actual_date"], expected=f'>={p["enrollment_date"]}')

    for p in participants:
        pid = p["participant_id"]
        counts: dict[str, list[str]] = defaultdict(list)
        for v in visits_by_pid[pid]:
            counts[v["visit_code"]].append(v["visit_id"])
        for code in expected_visits:
            if code not in counts:
                add("V001", "ERROR", "visits", f"{pid}/{code}", participant_id=pid,
                    observed="missing", expected=code)
        for code, ids in counts.items():
            for vid in sorted(ids)[1:]:
                add("V003", "ERROR", "visits", vid, participant_id=pid, visit_id=vid,
                    observed=code, expected="one visit with this code per participant")

    for table in ("vital_signs", "laboratory_results"):
        specs = config[table]
        measurements_by_visit: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for m in _rows(con, table, dataset_id):
            mid, vid, code = m["measurement_id"], m["visit_id"], m["test_code"]
            pid = by_visit[vid]["participant_id"]
            measurements_by_visit[vid][code].append(mid)
            if code not in specs:
                add("M001", "ERROR", table, mid, participant_id=pid, visit_id=vid,
                    observed=code, expected=", ".join(specs))
                continue
            spec = specs[code]
            if m["unit"] != spec["unit"]:
                add("M002", "ERROR", table, mid, participant_id=pid, visit_id=vid,
                    observed=m["unit"], expected=spec["unit"])
            low, high = Decimal(str(spec["review_min"])), Decimal(str(spec["review_max"]))
            if not low <= m["result_value"] <= high:
                add("M003", "WARNING", table, mid, participant_id=pid, visit_id=vid,
                    observed=m["result_value"], expected=f"{low}..{high} (synthetic study review bounds)")
        for v in visits:
            vid, pid = v["visit_id"], v["participant_id"]
            codes = measurements_by_visit.get(vid, {})
            for code in specs:
                if code not in codes:
                    add("M004", "ERROR", table, f"{vid}/{code}", participant_id=pid, visit_id=vid,
                        observed="missing", expected=code)
                for mid in sorted(codes.get(code, []))[1:]:
                    add("M005", "ERROR", table, mid, participant_id=pid, visit_id=vid,
                        observed=code, expected="one measurement with this code per visit")

    last_visit_day = max(expected_visits.values())
    for ae in _rows(con, "adverse_events", dataset_id):
        aid, pid = ae["event_id"], ae["participant_id"]
        enroll = by_participant[pid]["enrollment_date"]
        if ae["end_date"] is not None and ae["end_date"] < ae["start_date"]:
            add("A001", "ERROR", "adverse_events", aid, participant_id=pid,
                observed=ae["end_date"], expected=f'>={ae["start_date"]}')
        if ae["start_date"] < enroll:
            add("A002", "ERROR", "adverse_events", aid, participant_id=pid,
                observed=ae["start_date"], expected=f'>={enroll}')
        if ae["severity"] not in config["allowed_adverse_event_severity"]:
            add("A003", "WARNING", "adverse_events", aid, participant_id=pid,
                observed=ae["severity"], expected=", ".join(config["allowed_adverse_event_severity"]))
        if ae["event_term"] not in config["allowed_adverse_event_terms"]:
            add("A004", "WARNING", "adverse_events", aid, participant_id=pid,
                observed=ae["event_term"], expected=", ".join(config["allowed_adverse_event_terms"]))
        limit = enroll + timedelta(days=last_visit_day) + window
        if ae["start_date"] > limit:
            add("A005", "WARNING", "adverse_events", aid, participant_id=pid,
                observed=ae["start_date"], expected=f'<= {limit}')

    return sorted(issue_map.values(), key=lambda row: (row["rule_id"], row["source_table"], row["record_id"]))


def _resolve_dataset(con: Any, dataset_id: str | None, study_id: str) -> str:
    rows = con.execute("SELECT dataset_id, study_id FROM datasets ORDER BY dataset_id").fetchall()
    if not rows:
        raise QualityError("No datasets found. Import synthetic data first.")
    if dataset_id is None and len(rows) > 1:
        raise QualityError("More than one dataset loaded; use --dataset-id to choose one.")
    chosen = dataset_id if dataset_id is not None else rows[0][0]
    match = next((r for r in rows if r[0] == chosen), None)
    if match is None:
        raise QualityError(f"Dataset not found: {chosen}")
    if match[1] != study_id:
        raise QualityError(f"Rules target study {study_id}, but the selected dataset belongs to {match[1]}")
    return chosen


def evaluate_dataset(
    db_path: Path,
    config_path: Path = Path("configs/quality_rules.json"),
    *,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Atomically replace this dataset's findings and retain each run's summary."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise QualityError(f"Database not found: {db_path}")
    config, config_sha = load_rules(config_path)
    con = _db_module().connect(str(db_path.resolve()))
    try:
        initialize_schema(con)  # Safe on existing databases: creates only missing tables.
        con.execute("BEGIN TRANSACTION")
        try:
            chosen = _resolve_dataset(con, dataset_id, config["study_id"])
            issues = inspect_dataset(con, chosen, config)
            run_id = str(uuid.uuid4())
            con.execute(
                "INSERT INTO quality_runs (run_id, dataset_id, ruleset_sha256, issue_count) VALUES (?, ?, ?, ?)",
                [run_id, chosen, config_sha, len(issues)],
            )
            con.execute("DELETE FROM quality_issues WHERE dataset_id = ?", [chosen])
            for issue in issues:
                con.execute(
                    "INSERT INTO quality_issues (dataset_id, issue_id, run_id, rule_id, severity, source_table, "
                    "record_id, participant_id, visit_id, message, observed_value, expected_value) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [issue["dataset_id"], issue["issue_id"], run_id, issue["rule_id"],
                     issue["severity"], issue["source_table"], issue["record_id"],
                     issue["participant_id"], issue["visit_id"], issue["message"],
                     issue["observed_value"], issue["expected_value"]],
                )
            con.execute("COMMIT")
            return {
                "status": "EVALUATED", "dataset_id": chosen, "run_id": run_id,
                "ruleset_sha256": config_sha, "rule_count": len(RULES),
                "issue_count": len(issues),
                "by_severity": dict(sorted(Counter(x["severity"] for x in issues).items())),
                "by_rule": dict(sorted(Counter(x["rule_id"] for x in issues).items())),
            }
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def current_issues(db_path: Path, dataset_id: str) -> list[dict[str, Any]]:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise QualityError(f"Database not found: {db_path}")
    con = _db_module().connect(str(db_path.resolve()), read_only=True)
    try:
        result = con.execute(
            "SELECT dataset_id, issue_id, rule_id, severity, source_table, record_id, "
            "participant_id, visit_id, message, observed_value, expected_value "
            "FROM quality_issues WHERE dataset_id = ? "
            "ORDER BY rule_id, source_table, record_id",
            [dataset_id],
        )
        return [dict(zip(REPORT_FIELDS, row)) for row in result.fetchall()]
    finally:
        con.close()


def write_report(db_path: Path, dataset_id: str, path: Path) -> Path:
    """Export only review findings. Never export patient records to this report."""
    rows = current_issues(db_path, dataset_id)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Review synthetic clinical-study records")
    parser.add_argument("--db", type=Path, default=Path("data/study.duckdb"))
    parser.add_argument("--rules", type=Path, default=Path("configs/quality_rules.json"))
    parser.add_argument("--dataset-id", help="Required when more than one generated dataset is loaded")
    parser.add_argument("--report", type=Path, help="Optional CSV report path")
    args = parser.parse_args()
    try:
        result = evaluate_dataset(args.db, args.rules, dataset_id=args.dataset_id)
        if args.report is not None:
            path = write_report(args.db, result["dataset_id"], args.report)
            result["report"] = str(path)
    except (QualityError, OSError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
