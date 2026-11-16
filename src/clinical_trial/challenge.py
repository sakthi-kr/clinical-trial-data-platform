"""Deterministic fault-injection and exact-match scoring for synthetic study data.

This tool operates on a separate generated CSV fixture, never on real records.
The reference labels describe *rule findings*, not medical ground truth.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from clinical_trial.quality import (
    RULES, current_issues, evaluate_dataset, load_rules, write_report,
)
from clinical_trial.schema import TABLE_COLUMNS
from clinical_trial.validate_source import check_source


class ChallengeError(ValueError):
    """A benchmark fixture or result is invalid."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_table(folder: Path, name: str) -> list[dict[str, str]]:
    with (folder / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if tuple(reader.fieldnames or ()) != TABLE_COLUMNS[name]:
            raise ChallengeError(f"Incorrect table header: {name}")
        return list(reader)


def _write_table(folder: Path, name: str, rows: list[dict[str, str]]) -> None:
    with (folder / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TABLE_COLUMNS[name], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _issue_key(rule: str, table: str, record: str) -> tuple[str, str, str]:
    if rule not in RULES or table not in TABLE_COLUMNS or not record:
        raise ChallengeError(f"Invalid ground-truth label: {(rule, table, record)}")
    return rule, table, record


def _inject(tables: dict[str, list[dict[str, str]]], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Inject all supported rule families with disjoint source-record edits.

    Multi-label cases explicitly include the deterministic secondary findings.
    Cases reference *exact* rule, source table and record identifier.
    """
    participants = tables["participants"]
    events = tables["adverse_events"]
    if len(participants) < 18 or len(events) < 5:
        raise ChallengeError("Need at least 18 participants and 5 synthetic adverse events; generate the default 500-participant study")
    expected_visits = [v["visit_code"] for v in rules["visits"]]
    if "BASELINE" not in expected_visits or "WEEK04" not in expected_visits or "WEEK08" not in expected_visits:
        raise ChallengeError("The deterministic challenge expects BASELINE, WEEK04 and WEEK08 visit codes")

    by_pid = {p["participant_id"]: p for p in participants}
    visit_lookup = {(v["participant_id"], v["visit_code"]): v for v in tables["visits"]}
    cases: list[dict[str, Any]] = []

    def participant(n: int) -> dict[str, str]:
        return participants[n - 1]

    def visit(n: int, code: str = "BASELINE") -> dict[str, str]:
        key = (participant(n)["participant_id"], code)
        if key not in visit_lookup:
            raise ChallengeError(f"Missing expected baseline visit {key}")
        return visit_lookup[key]

    def measurement(table: str, n: int, test_code: str, visit_code: str = "BASELINE") -> dict[str, str]:
        vid = visit(n, visit_code)["visit_id"]
        matches = [x for x in tables[table] if x["visit_id"] == vid and x["test_code"] == test_code]
        if len(matches) != 1:
            raise ChallengeError(f"Expected exactly one {test_code} at {vid}, got {len(matches)}")
        return matches[0]

    def add(case_id: str, description: str, labels: list[tuple[str, str, str]]) -> None:
        cases.append({"case_id": case_id, "mutation": description,
                      "expected_findings": [dict(zip(("rule_id", "source_table", "record_id"), _issue_key(*x))) for x in labels]})

    # Participant checks: different participants, preserving all structural keys.
    p = participant(15); p["age_years"] = str(rules["age_years"]["max"] + 16)
    add("invalid_age", "Age outside fictional eligibility bounds", [("P001", "participants", p["participant_id"])])
    p = participant(16); p["study_group"] = "UNKNOWN_ARM"
    add("unknown_group", "Unknown study arm", [("P002", "participants", p["participant_id"])])
    p = participant(17); p["recorded_sex"] = "X"
    add("unknown_sex", "Unknown recorded-sex code", [("P003", "participants", p["participant_id"])])

    # Visit deletion must cascade to child CSVs before structural preflight.
    v = visit(4, "WEEK08")
    vid, pid, code = v["visit_id"], v["participant_id"], v["visit_code"]
    tables["visits"].remove(v)
    for table in ("vital_signs", "laboratory_results"):
        tables[table] = [x for x in tables[table] if x["visit_id"] != vid]
    add("missing_visit", "Expected visit absent, including its child measurements", [("V001", "visits", f"{pid}/{code}")])

    v = visit(5, "WEEK04")
    original_code = v["visit_code"]
    v["visit_code"] = "UNSCHEDULED"
    add("unrecognized_visit", "Unknown visit code replaces scheduled visit", [
        ("V002", "visits", v["visit_id"]), ("V001", "visits", f"{v['participant_id']}/{original_code}")])

    v = visit(6, "WEEK08")
    clone_id = "ZV0000001"
    if any(x["visit_id"] == clone_id for x in tables["visits"]):
        raise ChallengeError(f"Injected visit ID already exists: {clone_id}")
    clone = dict(v, visit_id=clone_id)
    tables["visits"].append(clone)
    for table, prefix in (("vital_signs", "ZVS"), ("laboratory_results", "ZLB")):
        original = [x for x in tables[table] if x["visit_id"] == v["visit_id"]]
        for i, row in enumerate(original, 1):
            copy = dict(row, visit_id=clone_id, measurement_id=f"{prefix}{i:06d}")
            if any(x["measurement_id"] == copy["measurement_id"] for x in tables[table]):
                raise ChallengeError("Injected measurement ID collision")
            tables[table].append(copy)
    add("duplicate_visit", "Additional visit with same participant and visit code, including all expected measurements", [("V003", "visits", clone_id)])

    v = visit(7, "WEEK04")
    v["planned_date"] = (date.fromisoformat(v["planned_date"]) + timedelta(days=1)).isoformat()
    add("wrong_planned_date", "Incorrect planned visit date", [("V004", "visits", v["visit_id"])])
    v = visit(8, "WEEK04")
    v["actual_date"] = (date.fromisoformat(v["planned_date"]) + timedelta(days=rules["visit_window_days"] + 5)).isoformat()
    add("late_visit", "Visit recorded outside allowed time window", [("V005", "visits", v["visit_id"])])
    v = visit(9)
    enrolled = date.fromisoformat(by_pid[v["participant_id"]]["enrollment_date"])
    v["actual_date"] = (enrolled - timedelta(days=1)).isoformat()
    add("visit_before_enrollment", "Baseline recorded before enrolment; also outside visit window", [
        ("V006", "visits", v["visit_id"]), ("V005", "visits", v["visit_id"])])

    m = measurement("vital_signs", 10, "SBP")
    standard_code = m["test_code"]
    m["test_code"] = "UNKNOWN_TEST"
    add("unrecognized_measurement", "Unknown measurement replaces SBP", [
        ("M001", "vital_signs", m["measurement_id"]), ("M004", "vital_signs", f"{m['visit_id']}/{standard_code}")])
    m = measurement("vital_signs", 11, "SBP")
    m["unit"] = "kPa"
    add("unexpected_unit", "Mismatched unit without conversion", [("M002", "vital_signs", m["measurement_id"])])
    m = measurement("laboratory_results", 12, "GLU")
    m["result_value"] = str(rules["laboratory_results"]["GLU"]["review_max"] + 900)
    add("out_of_review_range", "Synthetic observation above configured review bound", [("M003", "laboratory_results", m["measurement_id"])])
    m = measurement("laboratory_results", 13, "HGB")
    tables["laboratory_results"].remove(m)
    add("missing_measurement", "Expected HGB measurement deleted", [("M004", "laboratory_results", f"{m['visit_id']}/HGB")])
    m = measurement("vital_signs", 14, "SBP")
    duplicate_id = "ZVS9999999"
    if any(x["measurement_id"] == duplicate_id for x in tables["vital_signs"]):
        raise ChallengeError("Injected duplicate-measurement identifier collision")
    tables["vital_signs"].append(dict(m, measurement_id=duplicate_id))
    add("duplicate_measurement", "Second SBP measurement within the same visit", [("M005", "vital_signs", duplicate_id)])

    # Five separate event records avoid artificially conflating adverse-event errors.
    e = events[0]; e["end_date"] = (date.fromisoformat(e["start_date"]) - timedelta(days=2)).isoformat()
    add("event_end_before_start", "Adverse-event chronology reversed", [("A001", "adverse_events", e["event_id"])])
    e = events[1]; enrolled = date.fromisoformat(by_pid[e["participant_id"]]["enrollment_date"])
    e["start_date"] = (enrolled - timedelta(days=1)).isoformat()
    add("event_before_enrollment", "Adverse event begins before enrolment", [("A002", "adverse_events", e["event_id"])])
    e = events[2]; e["severity"] = "UNKNOWN"
    add("event_severity_code", "Unknown adverse-event severity", [("A003", "adverse_events", e["event_id"])])
    e = events[3]; e["event_term"] = "UNMAPPED_EVENT"
    add("event_term_code", "Unknown adverse-event term", [("A004", "adverse_events", e["event_id"])])
    e = events[4]; enrolled = date.fromisoformat(by_pid[e["participant_id"]]["enrollment_date"])
    last_day = max(v["day"] for v in rules["visits"])
    e["start_date"] = (enrolled + timedelta(days=last_day + rules["visit_window_days"] + 8)).isoformat()
    e["end_date"] = ""  # Ongoing, avoiding an unintended A001 finding.
    add("event_after_followup", "Adverse event begins after fictional study follow-up", [("A005", "adverse_events", e["event_id"])])

    all_keys = [_issue_key(x["rule_id"], x["source_table"], x["record_id"])
                for case in cases for x in case["expected_findings"]]
    if len(all_keys) != len(set(all_keys)) or set(k[0] for k in all_keys) != set(RULES):
        raise ChallengeError("Ground-truth labels must be unique and cover every quality rule")
    return cases


def inject_fixture(source_dir: Path, output_dir: Path, rules_path: Path) -> dict[str, Any]:
    """Copy a clean synthetic source and create a separate structurally valid fixture."""
    source_dir, output_dir = Path(source_dir), Path(output_dir)
    if source_dir.resolve() == output_dir.resolve():
        raise ChallengeError("Output must be separate from the clean source")
    if output_dir.exists():
        raise ChallengeError(f"Output already exists; refusing to overwrite: {output_dir}")
    rules, rules_sha = load_rules(rules_path)
    source = check_source(source_dir)
    if source["study_id"] != rules["study_id"]:
        raise ChallengeError("Study ID in source and rules does not match")
    if source.get("synthetic_only") is not True:
        raise ChallengeError("This benchmark requires synthetic-only data")
    tables = {table: _read_table(source_dir, table) for table in TABLE_COLUMNS}
    cases = _inject(tables, rules)
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["benchmark_fixture"] = {
            "name": "deterministic_rule_challenge_v1",
            "clean_source_sha256": _sha((source_dir / "manifest.json").read_bytes()),
        }
        for table in TABLE_COLUMNS:
            _write_table(output_dir, table, tables[table])
            file = output_dir / f"{table}.csv"
            manifest["tables"][table]["rows"] = len(tables[table])
            manifest["tables"][table]["sha256"] = _sha(file.read_bytes())
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        fixture_sha = _sha((output_dir / "manifest.json").read_bytes())
        truth = {
            "synthetic_only": True,
            "fixture_manifest_sha256": fixture_sha,
            "clean_manifest_sha256": manifest["benchmark_fixture"]["clean_source_sha256"],
            "ruleset_sha256": rules_sha,
            "cases": cases,
        }
        (output_dir / "ground_truth.json").write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # The fixture must still be accepted by the production structural checks.
        check_source(output_dir)
        return {"status": "CREATED", "output_dir": str(output_dir), "case_count": len(cases),
                "expected_findings": sum(len(x["expected_findings"]) for x in cases),
                "rules_covered": len(RULES), "dataset_id": fixture_sha}
    except Exception:
        shutil.rmtree(output_dir)
        raise


def _metrics(expected: set[tuple[str, str, str]], found: set[tuple[str, str, str]]) -> dict[str, Any]:
    tp, fp, fn = len(expected & found), len(found - expected), len(expected - found)
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p is not None and r is not None and p + r else (0.0 if p is not None and r is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def score_fixture(db_path: Path, truth_path: Path, rules_path: Path, report_dir: Path | None = None) -> dict[str, Any]:
    """Evaluate a loaded fixture and compare exact rule/table/record labels.

    Precision and recall use injected *rule-level findings*, not clinical events.
    No true-negative count is available, so accuracy/specificity are not reported.
    """
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    _, rules_sha = load_rules(rules_path)
    if truth.get("synthetic_only") is not True or not isinstance(truth.get("cases"), list):
        raise ChallengeError("Invalid synthetic ground-truth file")
    if rules_sha != truth.get("ruleset_sha256"):
        raise ChallengeError("Rules file differs from the configuration used for fault injection")
    dataset_id = truth.get("fixture_manifest_sha256")
    if not isinstance(dataset_id, str) or len(dataset_id) != 64:
        raise ChallengeError("Invalid fixture dataset ID")
    expected: set[tuple[str, str, str]] = set()
    case_results = []
    for case in truth["cases"]:
        case_keys = {_issue_key(x["rule_id"], x["source_table"], x["record_id"])
                     for x in case["expected_findings"]}
        if expected.intersection(case_keys):
            raise ChallengeError("Repeated ground-truth finding in multiple cases")
        expected.update(case_keys)
        case_results.append({"case_id": case["case_id"], "expected": len(case_keys)})
    # Validate all reference labels before making any database writes.
    if not expected or {x[0] for x in expected} != set(RULES):
        raise ChallengeError("Ground truth must cover every supported rule")
    # The dataset is selected explicitly even if clean batches coexist in the DB.
    evaluation = evaluate_dataset(db_path, rules_path, dataset_id=dataset_id)
    found_rows = current_issues(db_path, dataset_id)
    found = {_issue_key(x["rule_id"], x["source_table"], x["record_id"]) for x in found_rows}
    if len(found) != len(found_rows):
        raise ChallengeError("Duplicate detected finding keys")
    all_by_rule = {rule: _metrics({x for x in expected if x[0] == rule},
                                   {x for x in found if x[0] == rule}) for rule in sorted(RULES)}
    for case in case_results:
        entry = next(x for x in truth["cases"] if x["case_id"] == case["case_id"])
        labels = {_issue_key(x["rule_id"], x["source_table"], x["record_id"])
                  for x in entry["expected_findings"]}
        case["found"] = len(labels & found)
        case["passed"] = labels <= found
    result = {
        "status": "SCORED", "dataset_id": dataset_id, "quality_run_id": evaluation["run_id"],
        "expected_findings": len(expected), "detected_findings": len(found),
        "overall": _metrics(expected, found), "by_rule": all_by_rule, "cases": case_results,
        "false_positives": [dict(zip(("rule_id", "source_table", "record_id"), x)) for x in sorted(found - expected)],
        "false_negatives": [dict(zip(("rule_id", "source_table", "record_id"), x)) for x in sorted(expected - found)],
        "interpretation": "Agreement with synthetic injected rule labels only; not clinical accuracy or regulatory validation",
    }
    if report_dir is not None:
        target = Path(report_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "benchmark.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_report(db_path, dataset_id, target / "detected_issues.csv")
        result["report_dir"] = str(target)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic quality-rule challenge and exact-match scoring")
    sub = parser.add_subparsers(dest="command", required=True)
    inject = sub.add_parser("inject", help="Create a separate, intentionally corrupted synthetic CSV fixture")
    inject.add_argument("--source", type=Path, default=Path("data/generated"))
    inject.add_argument("--output", type=Path, default=Path("data/challenge"))
    inject.add_argument("--rules", type=Path, default=Path("configs/quality_rules.json"))
    score = sub.add_parser("score", help="Evaluate a loaded fixture against expected findings")
    score.add_argument("--db", type=Path, default=Path("data/challenge.duckdb"))
    score.add_argument("--truth", type=Path, default=Path("data/challenge/ground_truth.json"))
    score.add_argument("--rules", type=Path, default=Path("configs/quality_rules.json"))
    score.add_argument("--report-dir", type=Path, default=Path("data/benchmark"))
    args = parser.parse_args()
    try:
        if args.command == "inject":
            result = inject_fixture(args.source, args.output, args.rules)
        else:
            result = score_fixture(args.db, args.truth, args.rules, args.report_dir)
    except (ChallengeError, ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
