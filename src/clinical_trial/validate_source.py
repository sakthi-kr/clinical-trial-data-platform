"""Phase-2 structural and type checks before transactional DuckDB ingestion.

This is intentionally NOT the clinical rules engine (Phase 3). Unexpected
measurement ranges and protocol deviations are preserved for later review.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from clinical_trial.schema import TABLE_COLUMNS
from clinical_trial.verify import verify_manifest

PK_FIELDS = {
    "sites": "site_id", "participants": "participant_id", "visits": "visit_id",
    "vital_signs": "measurement_id", "laboratory_results": "measurement_id",
    "adverse_events": "event_id",
}
DATE_FIELDS = {
    "participants": ("enrollment_date",),
    "visits": ("planned_date", "actual_date"),
    "adverse_events": ("start_date", "end_date"),
}
OPTIONAL_FIELDS = {("adverse_events", "end_date")}
DECIMAL_FIELDS = {("vital_signs", "result_value"), ("laboratory_results", "result_value")}
INTEGER_FIELDS = {("participants", "age_years")}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INTEGER_RE = re.compile(r"^\d+$")


class SourceValidationError(ValueError):
    """Invalid or inconsistent source files; never partially import."""


def check_source(input_dir: Path) -> dict[str, Any]:
    """Check manifest, exact headers, scalar types, keys and relationships.

    Returns parsed manifest for provenance. All six tables remain source files;
    no clinical threshold or treatment efficacy checks are performed.
    """
    input_dir = Path(input_dir)
    problems = verify_manifest(input_dir)
    if problems:
        raise SourceValidationError("Manifest verification failed: " + "; ".join(problems))
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest.get("study_id"), str) or not manifest["study_id"].strip():
        raise SourceValidationError("manifest: missing study_id")
    # Parent tables arrive before child tables in TABLE_COLUMNS order.
    keys: dict[str, set[str]] = {}
    relationships = {
        "participants": ("site_id", "sites"),
        "visits": ("participant_id", "participants"),
        "vital_signs": ("visit_id", "visits"),
        "laboratory_results": ("visit_id", "visits"),
        "adverse_events": ("participant_id", "participants"),
    }
    for table, columns in TABLE_COLUMNS.items():
        path = input_dir / f"{table}.csv"
        pk = PK_FIELDS[table]
        seen: set[str] = set()
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if tuple(reader.fieldnames or []) != columns:
                raise SourceValidationError(f"{table}: unexpected columns")
            for line_no, record in enumerate(reader, start=2):
                if None in record or any(value is None for value in record.values()):
                    raise SourceValidationError(f"{table}:{line_no}: malformed column count")
                for column in columns:
                    value = record[column]
                    if not value and (table, column) not in OPTIONAL_FIELDS:
                        raise SourceValidationError(f"{table}:{line_no}: missing {column}")
                    if column in DATE_FIELDS.get(table, ()) and value:
                        if not DATE_RE.fullmatch(value):
                            raise SourceValidationError(f"{table}:{line_no}: invalid ISO date {column}")
                        try:
                            date.fromisoformat(value)
                        except ValueError as exc:
                            raise SourceValidationError(f"{table}:{line_no}: invalid ISO date {column}") from exc
                    if (table, column) in INTEGER_FIELDS:
                        if not INTEGER_RE.fullmatch(value):
                            raise SourceValidationError(f"{table}:{line_no}: invalid integer {column}")
                    if (table, column) in DECIMAL_FIELDS:
                        try:
                            number = Decimal(value)
                            if not number.is_finite():
                                raise ValueError("not finite")
                            if number.as_tuple().exponent < -6:
                                raise ValueError("more than six decimal places")
                        except (InvalidOperation, ValueError) as exc:
                            raise SourceValidationError(f"{table}:{line_no}: invalid numeric {column}") from exc
                identity = record[pk]
                if identity in seen:
                    raise SourceValidationError(f"{table}:{line_no}: duplicate {pk}={identity}")
                seen.add(identity)
                if table in relationships:
                    field, parent_table = relationships[table]
                    if record[field] not in keys[parent_table]:
                        raise SourceValidationError(
                            f"{table}:{line_no}: unknown {parent_table} {field}={record[field]}"
                        )
        keys[table] = seen
    return manifest
