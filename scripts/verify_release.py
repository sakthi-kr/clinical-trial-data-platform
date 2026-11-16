"""Check the complete synthetic-study workflow in fresh temporary directories.

The run uses only fictional records and does not modify the checked-out repo.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from clinical_trial.dashboard_data import load_snapshot
from clinical_trial.review import record_review

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _run(*arguments: str) -> dict:
    command = [PYTHON, "-m", *arguments]
    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )
    stdout = completed.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Expected JSON from {' '.join(command)}; got:\n{stdout}\n{completed.stderr}"
        ) from exc


def _run_text(*arguments: str) -> str:
    completed = subprocess.run(
        [PYTHON, "-m", *arguments], check=True, cwd=ROOT,
        text=True, capture_output=True,
    )
    return completed.stdout.strip()


def verify() -> dict:
    with tempfile.TemporaryDirectory(prefix="clinical-study-release-") as scratch:
        base = Path(scratch)
        clean, challenge = base / "clean", base / "challenge"
        clean_db, challenge_db = base / "clean.duckdb", base / "challenge.duckdb"
        generated = _run(
            "clinical_trial.generate", "--participants", "180", "--output-dir", str(clean)
        )
        assert generated["participants_requested"] == 180, generated
        original_manifest = hashlib.sha256((clean / "manifest.json").read_bytes()).hexdigest()
        assert _run_text("clinical_trial.verify", "--input-dir", str(clean)).startswith("PASS:")
        clean_import = _run(
            "clinical_trial.ingest", "--input-dir", str(clean), "--db", str(clean_db)
        )
        assert clean_import["status"] == "LOADED", clean_import
        clean_review = _run("clinical_trial.quality", "--db", str(clean_db))
        assert clean_review["issue_count"] == 0, clean_review
        injected = _run(
            "clinical_trial.challenge", "inject", "--source", str(clean),
            "--output", str(challenge)
        )
        assert injected["case_count"] == 19, injected
        assert _run_text("clinical_trial.verify", "--input-dir", str(challenge)).startswith("PASS:")
        challenge_import = _run(
            "clinical_trial.ingest", "--input-dir", str(challenge),
            "--db", str(challenge_db)
        )
        assert challenge_import["status"] == "LOADED", challenge_import
        benchmark = _run(
            "clinical_trial.challenge", "score", "--db", str(challenge_db),
            "--truth", str(challenge / "ground_truth.json"),
            "--report-dir", str(base / "benchmark")
        )
        scores = benchmark["overall"]
        assert (scores["tp"], scores["fp"], scores["fn"]) == (22, 0, 0), benchmark
        snapshot = load_snapshot(challenge_db, challenge_import["dataset_id"])
        assert len(snapshot["issues"]) == 22, snapshot
        selected = snapshot["issues"][0]
        first = record_review(
            challenge_db, dataset_id=challenge_import["dataset_id"],
            issue_id=selected["issue_id"], status="IN_REVIEW",
            reviewer="Release demo", note="Reviewing synthetic record",
            expected_version=0,
        )
        second = record_review(
            challenge_db, dataset_id=challenge_import["dataset_id"],
            issue_id=selected["issue_id"], status="CONFIRMED",
            reviewer="Release demo", note="Synthetic issue confirmed",
            expected_version=1,
        )
        assert (first["review_version"], second["review_version"]) == (1, 2)
        reviewed = load_snapshot(challenge_db, challenge_import["dataset_id"])
        assert len(reviewed["review_history"]) == 2
        assert next(x for x in reviewed["issues"] if x["issue_id"] == selected["issue_id"])["review_status"] == "CONFIRMED"
        exported = _run(
            "clinical_trial.export_domains", "--db", str(challenge_db),
            "--dataset-id", challenge_import["dataset_id"],
            "--output-dir", str(base / "exports")
        )
        assert exported["status"] == "EXPORTED", exported
        assert exported["rows"]["DM"] == 180, exported
        manifest = json.loads((base / "exports" / "mapping_manifest.json").read_text())
        assert manifest["synthetic_only"] is True
        assert manifest["regulatory_compliant"] is False
        assert set(manifest["domains"]) == {"DM", "SV", "VS", "LB", "AE"}
        assert _run_text("clinical_trial.verify", "--input-dir", str(clean)).startswith("PASS:")
        assert hashlib.sha256((clean / "manifest.json").read_bytes()).hexdigest() == original_manifest
        return {
            "result": "PASS",
            "synthetic_participants": 180,
            "clean_quality_issues": clean_review["issue_count"],
            "injected_cases": injected["case_count"],
            "benchmark": {"tp": scores["tp"], "fp": scores["fp"], "fn": scores["fn"]},
            "review_events": len(reviewed["review_history"]),
            "exported_domains": sorted(manifest["domains"]),
            "original_manifest_unchanged": True,
        }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2, sort_keys=True))
