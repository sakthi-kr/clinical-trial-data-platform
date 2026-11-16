"""Local review decisions for synthetic data-quality findings.

Review metadata is append-only at the application layer. No source clinical
study data or quality-rule outputs are modified by reviewer actions.
This is not a regulated, authenticated or tamper-proof audit system.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Mapping

from clinical_trial.ingest import _db_module, initialize_schema

STATUS_CHOICES = ("IN_REVIEW", "CONFIRMED", "DISMISSED")
DEFAULT_STATUS = "UNREVIEWED"
FINGERPRINT_FIELDS = (
    "rule_id", "severity", "source_table", "record_id", "observed_value", "expected_value",
)


class ReviewError(ValueError):
    """An invalid or outdated review action was rejected."""


def finding_signature(finding: Mapping[str, Any]) -> str:
    """Bind review decisions to the actual content of a current finding."""
    payload = {key: finding.get(key) for key in FINGERPRINT_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _review_events_exist(con: Any) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='issue_review_events'"
    ).fetchone())


def reviews_for_current(
    con: Any, dataset_id: str, issues: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Enrich the current issue list with matching, most recent review events.

    Previous reviews remain in history, but only reviews for the same issue
    *and unchanged finding content* apply to the current issue list.
    """
    current = {issue["issue_id"]: finding_signature(issue) for issue in issues}
    statuses: dict[str, dict[str, Any]] = {
        issue_id: {"review_status": DEFAULT_STATUS, "review_version": 0,
                   "reviewer": "", "review_note": ""}
        for issue_id in current
    }
    if not _review_events_exist(con):
        return statuses, []
    results = con.execute(
        """SELECT event_id, issue_id, issue_signature, review_version, status,
                  reviewer, note, recorded_at
           FROM issue_review_events WHERE dataset_id = ?
           ORDER BY recorded_at ASC, review_version ASC, event_id ASC""",
        [dataset_id],
    )
    fields = [x[0] for x in results.description]
    events = [dict(zip(fields, row)) for row in results.fetchall()]
    for event in events:
        issue_id = event["issue_id"]
        if current.get(issue_id) != event["issue_signature"]:
            continue
        old = statuses[issue_id]
        if int(event["review_version"]) >= int(old["review_version"]):
            statuses[issue_id] = {
                "review_status": event["status"],
                "review_version": int(event["review_version"]),
                "reviewer": event["reviewer"],
                "review_note": event["note"],
            }
    return statuses, events


def record_review(
    db_path: Path,
    *,
    dataset_id: str,
    issue_id: str,
    status: str,
    reviewer: str,
    note: str,
    expected_version: int,
) -> dict[str, Any]:
    """Append one review event with optimistic version checking.

    Caller must provide the version observed in the dashboard. The database
    transaction guards against lost updates across multiple local sessions.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        raise ReviewError(f"Database does not exist: {db_path}")
    if status not in STATUS_CHOICES:
        raise ReviewError("Unsupported review status")
    reviewer = reviewer.strip()
    note = note.strip()
    if not (2 <= len(reviewer) <= 80):
        raise ReviewError("Reviewer label must be between 2 and 80 characters")
    if not (3 <= len(note) <= 1000):
        raise ReviewError("Review note must be between 3 and 1000 characters")
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
        raise ReviewError("Invalid expected review version")
    if not dataset_id or not issue_id:
        raise ReviewError("Dataset and issue identifiers are required")

    con = _db_module().connect(str(db_path.resolve()))
    try:
        # Existing databases gain the review table without changing source rows.
        initialize_schema(con)
        con.execute("BEGIN TRANSACTION")
        try:
            query = con.execute(
                """SELECT rule_id, severity, source_table, record_id,
                          observed_value, expected_value FROM quality_issues
                   WHERE dataset_id = ? AND issue_id = ?""",
                [dataset_id, issue_id],
            )
            found = query.fetchone()
            if not found:
                raise ReviewError("Current finding was not found; refresh the dashboard")
            row = dict(zip([x[0] for x in query.description], found))
            signature = finding_signature(row)
            latest = con.execute(
                """SELECT COALESCE(MAX(review_version), 0)
                   FROM issue_review_events
                   WHERE dataset_id=? AND issue_id=? AND issue_signature=?""",
                [dataset_id, issue_id, signature],
            ).fetchone()[0]
            if int(latest) != expected_version:
                raise ReviewError("Review changed in another session; refresh before saving")
            next_version = expected_version + 1
            event_id = str(uuid.uuid4())
            con.execute(
                """INSERT INTO issue_review_events
                     (event_id, dataset_id, issue_id, issue_signature,
                      review_version, status, reviewer, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [event_id, dataset_id, issue_id, signature,
                 next_version, status, reviewer, note],
            )
            con.execute("COMMIT")
            return {"event_id": event_id, "issue_id": issue_id,
                    "status": status, "review_version": next_version}
        except Exception:
            con.execute("ROLLBACK")
            raise
    except ReviewError:
        raise
    except Exception as exc:
        raise ReviewError(f"Could not record review: {exc}") from exc
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Review a synthetic data-quality finding")
    parser.add_argument("--db", type=Path, default=Path("data/challenge.duckdb"))
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--issue-id", required=True)
    parser.add_argument("--status", choices=STATUS_CHOICES, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--expected-version", type=int, default=0)
    args = parser.parse_args()
    try:
        result = record_review(
            args.db, dataset_id=args.dataset_id, issue_id=args.issue_id,
            status=args.status, reviewer=args.reviewer, note=args.note,
            expected_version=args.expected_version,
        )
    except ReviewError as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
