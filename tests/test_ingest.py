"""DuckDB integration tests; run when the runtime dependency is installed."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from clinical_trial.generate import generate_dataset
from clinical_trial.ingest import IngestionError, load_dataset, summarize
from clinical_trial.schema import TABLE_COLUMNS

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "study_protocol.json"


def make_source(tmp_path: Path, name="source", seed=7, n=24) -> Path:
    folder = tmp_path / name
    generate_dataset(CONFIG, folder, participant_count=n, seed=seed)
    return folder


def rewrite_with_updated_manifest(folder: Path, table: str, column: str, value: str) -> None:
    file = folder / f"{table}.csv"
    with file.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0][column] = value
    with file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tables"][table]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def test_import_and_typed_schema_and_file_provenance(tmp_path: Path):
    import duckdb
    source = make_source(tmp_path)
    db = tmp_path / "study.duckdb"
    result = load_dataset(source, db)
    assert result["status"] == "LOADED"
    assert result["counts"]["participants"] == 24
    assert result["counts"]["visits"] == 96
    assert result["counts"]["vital_signs"] == 192
    assert result["counts"]["laboratory_results"] == 384
    con = duckdb.connect(str(db), read_only=True)
    try:
        types = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='participants'").fetchall())
        assert types["age_years"] == "INTEGER"
        assert types["enrollment_date"] == "DATE"
        assert con.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 6
        assert con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1
        assert con.execute("SELECT DISTINCT event_type FROM ingestion_events").fetchall() == [("LOADED",)]
        for table in TABLE_COLUMNS:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == result["counts"][table]
    finally:
        con.close()


def test_repeat_import_skips_and_records_event(tmp_path: Path):
    import duckdb
    source = make_source(tmp_path)
    db = tmp_path / "study.duckdb"
    first = load_dataset(source, db)
    second = load_dataset(source, db)
    assert second["status"] == "SKIPPED"
    assert first["dataset_id"] == second["dataset_id"]
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 24
        assert con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM ingestion_events WHERE event_type='SKIPPED'").fetchone()[0] == 1
    finally:
        con.close()


def test_two_distinct_generated_batches_coexist(tmp_path: Path):
    source_a = make_source(tmp_path, name="a", seed=55)
    source_b = make_source(tmp_path, name="b", seed=56)
    db = tmp_path / "study.duckdb"
    a = load_dataset(source_a, db)
    b = load_dataset(source_b, db)
    assert a["dataset_id"] != b["dataset_id"]
    snap = summarize(db)
    assert len(snap["datasets"]) == 2
    assert all(d["counts"]["participants"] == 24 for d in snap["datasets"])


def test_corrupt_file_rejected_and_failure_logged(tmp_path: Path):
    source = make_source(tmp_path)
    db = tmp_path / "study.duckdb"
    (source / "participants.csv").write_bytes((source / "participants.csv").read_bytes() + b"oops\n")
    with pytest.raises(IngestionError, match="SHA-256 mismatch"):
        load_dataset(source, db)
    snap = summarize(db)
    assert snap["datasets"] == []
    assert snap["events"][0]["event_type"] == "FAILED"


def test_transaction_rollback_for_sql_cast_failure(tmp_path: Path):
    """Intentionally exceed DuckDB INTEGER while passing Python integer preflight."""
    source = make_source(tmp_path)
    db = tmp_path / "study.duckdb"
    rewrite_with_updated_manifest(source, "participants", "age_years", "99999999999999999")
    with pytest.raises(IngestionError, match="rolled back"):
        load_dataset(source, db)
    snap = summarize(db)
    assert snap["datasets"] == []
    assert len(snap["events"]) == 1
    assert snap["events"][0]["event_type"] == "FAILED"


def test_null_adverse_event_end_date(tmp_path: Path):
    import duckdb
    source = make_source(tmp_path, n=120)
    rewrite_with_updated_manifest(source, "adverse_events", "end_date", "")
    db = tmp_path / "study.duckdb"
    load_dataset(source, db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM adverse_events WHERE end_date IS NULL").fetchone()[0] >= 1
    finally:
        con.close()


def test_header_only_adverse_events_table_allowed(tmp_path: Path):
    """A small synthetic study may generate zero adverse events."""
    import duckdb
    source = make_source(tmp_path, n=5)
    file = source / "adverse_events.csv"
    with file.open("w", encoding="utf-8", newline="") as fp:
        fp.write("event_id,participant_id,event_term,severity,start_date,end_date\n")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tables"]["adverse_events"]["rows"] = 0
    manifest["tables"]["adverse_events"]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    db = tmp_path / "empty_events.duckdb"
    result = load_dataset(source, db)
    assert result["status"] == "LOADED"
    assert result["counts"]["adverse_events"] == 0
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM adverse_events").fetchone()[0] == 0
    finally:
        con.close()
