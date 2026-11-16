"""Read-only queries and presentation helpers for synthetic study quality data.

This module has no dependency on Streamlit or Plotly, so its data contracts can
be tested independently. It does not run evaluations or mutate the database.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from pathlib import Path
from typing import Any

from clinical_trial.ingest import _db_module


class DashboardDataError(RuntimeError):
    """The requested local study dataset is unavailable."""


EXPORT_COLUMNS = (
    "rule_id", "severity", "site_id", "visit_code", "source_table",
    "record_id", "participant_id", "visit_id", "message", "observed_value",
    "expected_value", "issue_id", "dataset_id",
)


def _open_readonly(db_path: Path) -> Any:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise DashboardDataError(f"Database file not found: {db_path}")
    try:
        return _db_module().connect(str(db_path.resolve()), read_only=True)
    except Exception as exc:
        raise DashboardDataError(f"Cannot open database for reading: {exc}") from exc


def _records(connection: Any, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    result = connection.execute(sql, params or [])
    columns = [col[0] for col in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def list_datasets(db_path: Path) -> list[dict[str, Any]]:
    """List loaded datasets without opening a writable connection."""
    con = _open_readonly(db_path)
    try:
        return _records(con, """
            SELECT dataset_id, study_id, loaded_at
            FROM datasets ORDER BY loaded_at DESC, dataset_id
        """)
    except Exception as exc:
        raise DashboardDataError(f"Cannot read dataset metadata: {exc}") from exc
    finally:
        con.close()


def load_snapshot(db_path: Path, dataset_id: str) -> dict[str, Any]:
    """Load the selected synthetic dataset's current issues and review history.

    The source is explicitly dataset-scoped: no current issues, measurements or
    participant data from a different import can be mixed into this view.
    """
    if not dataset_id or not isinstance(dataset_id, str):
        raise DashboardDataError("Select a dataset")
    con = _open_readonly(db_path)
    try:
        selected = _records(con, """
            SELECT dataset_id, study_id, loaded_at
            FROM datasets WHERE dataset_id = ?
        """, [dataset_id])
        if not selected:
            raise DashboardDataError("Selected dataset is not present in this database")

        counts: dict[str, int] = {}
        for table in ("participants", "visits", "vital_signs", "laboratory_results", "adverse_events"):
            # Project-owned fixed table names; never derived from UI input.
            counts[table] = int(con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE dataset_id = ?", [dataset_id]
            ).fetchone()[0])

        sites = _records(con, """
            SELECT site_id, site_name FROM sites
            WHERE dataset_id = ? ORDER BY site_id
        """, [dataset_id])
        visit_codes = _records(con, """
            SELECT DISTINCT visit_code FROM visits
            WHERE dataset_id = ? ORDER BY visit_code
        """, [dataset_id])
        history = _records(con, """
            SELECT run_id, evaluated_at, issue_count, ruleset_sha256
            FROM quality_runs
            WHERE dataset_id = ?
            ORDER BY evaluated_at DESC, run_id DESC
        """, [dataset_id])
        # The join to participants resolves the site for participant-level
        # findings. Missing expected visits use a synthetic record identifier
        # (participant/code) instead of a visit_id.
        issues = _records(con, """
            SELECT i.dataset_id, i.issue_id, i.rule_id, i.severity,
                   i.source_table, i.record_id, i.participant_id, i.visit_id,
                   COALESCE(p.site_id, 'N/A') AS site_id,
                   COALESCE(v.visit_code,
                     CASE WHEN i.rule_id = 'V001'
                       THEN split_part(i.record_id, '/', 2)
                       ELSE NULL END, 'N/A') AS visit_code,
                   i.message, i.observed_value, i.expected_value
            FROM quality_issues AS i
            LEFT JOIN participants AS p
                ON p.dataset_id = i.dataset_id
               AND p.participant_id = i.participant_id
            LEFT JOIN visits AS v
                ON v.dataset_id = i.dataset_id
               AND v.visit_id = i.visit_id
            WHERE i.dataset_id = ?
            ORDER BY i.rule_id, i.source_table, i.record_id
        """, [dataset_id])
        return {
            "dataset": selected[0],
            "counts": counts,
            "sites": sites,
            "visit_codes": [x["visit_code"] for x in visit_codes],
            "history": history,
            "issues": issues,
        }
    except DashboardDataError:
        raise
    except Exception as exc:
        raise DashboardDataError(f"Cannot load dashboard data: {exc}") from exc
    finally:
        con.close()


def filter_issues(
    issues: list[dict[str, Any]], *,
    site_id: str | None = None,
    visit_code: str | None = None,
    severity: str | None = None,
    rule_ids: list[str] | None = None,
    participant_text: str = "",
) -> list[dict[str, Any]]:
    """Apply combined filters without changing the underlying rows."""
    term = participant_text.strip().casefold()
    allowed = set(rule_ids) if rule_ids is not None else None
    return [row for row in issues
        if (site_id is None or row["site_id"] == site_id)
        and (visit_code is None or row["visit_code"] == visit_code)
        and (severity is None or row["severity"] == severity)
        and (allowed is None or row["rule_id"] in allowed)
        and (not term or term in str(row.get("participant_id") or "").casefold())
    ]


def count_by(issues: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Return stable chart labels and counts for the selected finding subset."""
    if key not in {"site_id", "visit_code", "rule_id", "severity"}:
        raise ValueError("Unsupported aggregation")
    counts = Counter(str(row.get(key) or "N/A") for row in issues)
    return [{"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def issue_csv(issues: list[dict[str, Any]]) -> bytes:
    """Export only issue metadata, never raw measurements or participant rows."""
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in issues:
        writer.writerow({key: row.get(key) for key in EXPORT_COLUMNS})
    return out.getvalue().encode("utf-8-sig")
