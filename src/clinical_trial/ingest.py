"""Transactional, idempotent, file-traceable DuckDB ingestion for Phase 2.

Usage:
  python -m clinical_trial.ingest --input-dir data/generated --db data/study.duckdb

No real health records. No automatic correction or rejection of clinically
unusual values: medical-style checks belong to Phase 3.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any

from clinical_trial.schema import TABLE_COLUMNS
from clinical_trial.validate_source import SourceValidationError, check_source


class IngestionError(RuntimeError):
    """Ingestion failed; no partial dataset should be committed."""


# Explicit trusted expressions; never interpolate user-supplied CSV headers into SQL.
EXPRESSIONS: dict[str, tuple[str, ...]] = {
    "sites": ("site_id", "site_name"),
    "participants": ("participant_id", "site_id", "study_group", "CAST(age_years AS INTEGER)",
                     "recorded_sex", "CAST(enrollment_date AS DATE)"),
    "visits": ("visit_id", "participant_id", "visit_code", "CAST(planned_date AS DATE)",
               "CAST(actual_date AS DATE)"),
    "vital_signs": ("measurement_id", "visit_id", "test_code", "CAST(result_value AS DECIMAL(18,6))", "unit"),
    "laboratory_results": ("measurement_id", "visit_id", "test_code", "CAST(result_value AS DECIMAL(18,6))", "unit"),
    "adverse_events": ("event_id", "participant_id", "event_term", "severity",
                       "CAST(start_date AS DATE)", "CAST(NULLIF(end_date, '') AS DATE)"),
}


def _db_module():
    try:
        import duckdb
    except ImportError as exc:
        raise IngestionError("DuckDB is not installed: run python -m pip install -e '.[dev]'") from exc
    return duckdb


def initialize_schema(connection: Any) -> None:
    schema = files("clinical_trial").joinpath("db_schema.sql").read_text(encoding="utf-8")
    connection.execute(schema)


def _write_event(connection: Any, dataset_id: str | None, kind: str, details: str) -> None:
    connection.execute(
        "INSERT INTO ingestion_events (event_id, dataset_id, event_type, details) VALUES (?, ?, ?, ?)",
        [str(uuid.uuid4()), dataset_id, kind, details[:1500]],
    )


def _insert_csv(connection: Any, dataset_id: str, table: str, path: Path) -> None:
    """Bulk load with explicit VARCHAR parsing and SQL type casts.

    Python preflight checks each record; DuckDB performs final typed insertion
    under the enclosing ACID transaction. It never ignores malformed rows.
    """
    columns = TABLE_COLUMNS[table]
    expr = ", ".join(EXPRESSIONS[table])
    # Use fixed project-owned column names; explicit column definitions avoid
    # accidental type inference and unexpected additional CSV columns.
    reader_columns = "{" + ", ".join(f"'{c}': 'VARCHAR'" for c in columns) + "}"
    sql = (
        f"INSERT INTO {table} (dataset_id, {', '.join(columns)}) "
        f"SELECT ? AS dataset_id, {expr} "
        f"FROM read_csv(?, header=true, auto_detect=false, columns={reader_columns}, "
        "delim=',', quote='\"', escape='\"', nullstr='', ignore_errors=false)"
    )
    connection.execute(sql, [dataset_id, str(path.resolve())])


def load_dataset(input_dir: Path, db_path: Path) -> dict[str, Any]:
    """Validate and atomically load a dataset, or log FAILED/SKIPPED.

    Dataset identity is based on the manifest's exact bytes, which themselves
    contain every source CSV's checksum. Two distinct seeds can coexist.
    """
    input_dir = Path(input_dir)
    db_path = Path(db_path)
    manifest_path = input_dir / "manifest.json"
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
    dataset_id = manifest_sha
    duckdb = _db_module()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path.resolve()))
    try:
        initialize_schema(con)
        try:
            manifest = check_source(input_dir)
            assert manifest_sha is not None
        except (SourceValidationError, OSError, ValueError, UnicodeError, csv.Error, AssertionError) as exc:
            _write_event(con, dataset_id, "FAILED", f"Preflight: {exc}")
            raise IngestionError(str(exc)) from exc

        if con.execute("SELECT 1 FROM datasets WHERE dataset_id = ?", [dataset_id]).fetchone():
            _write_event(con, dataset_id, "SKIPPED", "Exact source manifest already ingested")
            return {"status": "SKIPPED", "dataset_id": dataset_id, "reason": "Already loaded"}

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                "INSERT INTO datasets (dataset_id, study_id, manifest_sha256, source_directory, synthetic_only) "
                "VALUES (?, ?, ?, ?, TRUE)",
                [dataset_id, manifest["study_id"], manifest_sha, str(input_dir.resolve())],
            )
            counts: dict[str, int] = {}
            for table in TABLE_COLUMNS:
                info = manifest["tables"][table]
                con.execute(
                    "INSERT INTO source_files (dataset_id, table_name, file_name, sha256, source_rows) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [dataset_id, table, f"{table}.csv", info["sha256"], info["rows"]],
                )
                # Header-only CSVs are valid (e.g. no adverse events).
                # DuckDB need not parse an empty data body.
                if info["rows"]:
                    _insert_csv(con, dataset_id, table, input_dir / f"{table}.csv")
                actual = con.execute(f"SELECT COUNT(*) FROM {table} WHERE dataset_id = ?", [dataset_id]).fetchone()[0]
                if actual != info["rows"]:
                    raise IngestionError(f"{table}: loaded {actual} rows, manifest requires {info['rows']}")
                counts[table] = actual
            _write_event(con, dataset_id, "LOADED", f"Inserted {sum(counts.values())} rows")
            con.execute("COMMIT")
            return {"status": "LOADED", "dataset_id": dataset_id, "counts": counts}
        except Exception as exc:
            con.execute("ROLLBACK")
            _write_event(con, dataset_id, "FAILED", f"Transactional import: {type(exc).__name__}: {exc}")
            raise IngestionError(f"Import rolled back: {exc}") from exc
    finally:
        con.close()


def summarize(db_path: Path) -> dict[str, Any]:
    """Show datasets, per-table counts, and recent ingestion events."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise IngestionError(f"No database found: {db_path}")
    con = _db_module().connect(str(db_path.resolve()), read_only=True)
    try:
        ids = con.execute(
            "SELECT dataset_id, study_id, loaded_at FROM datasets ORDER BY loaded_at, dataset_id"
        ).fetchall()
        rows = []
        for dataset_id, study_id, loaded_at in ids:
            counts = {
                table: con.execute(f"SELECT COUNT(*) FROM {table} WHERE dataset_id = ?", [dataset_id]).fetchone()[0]
                for table in TABLE_COLUMNS
            }
            rows.append({"dataset_id": dataset_id, "study_id": study_id,
                         "loaded_at": str(loaded_at), "counts": counts})
        events = con.execute(
            "SELECT event_type, dataset_id, details FROM ingestion_events ORDER BY event_at, event_id"
        ).fetchall()
        return {"datasets": rows, "events": [dict(zip(("event_type", "dataset_id", "details"), r)) for r in events]}
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Transactional DuckDB import of synthetic clinical data")
    parser.add_argument("--input-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--db", type=Path, default=Path("data/study.duckdb"))
    parser.add_argument("--status", action="store_true", help="Summarize stored datasets and ingestions")
    args = parser.parse_args()
    try:
        result = summarize(args.db) if args.status else load_dataset(args.input_dir, args.db)
    except IngestionError as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
