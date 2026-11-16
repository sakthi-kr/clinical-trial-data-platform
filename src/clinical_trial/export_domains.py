"""Noncompliant, CDISC-inspired CSV views for wholly synthetic study data.

These are educational, simplified exports. They are NOT SDTM submission
packages, validated implementations or suitable for clinical use.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from clinical_trial.ingest import _db_module


class ExportError(ValueError):
    """The selected synthetic export cannot be produced."""


# Queries and column names are owned by this module, not derived from input.
DOMAIN_QUERIES: dict[str, tuple[tuple[str, ...], str]] = {
    "DM": (("STUDYID", "USUBJID", "SITEID", "ARM", "AGE", "SEX", "RFSTDTC"), """
        SELECT d.study_id, d.study_id || '-' || p.participant_id, p.site_id,
               p.study_group, p.age_years, p.recorded_sex, CAST(p.enrollment_date AS VARCHAR)
        FROM participants p JOIN datasets d ON d.dataset_id=p.dataset_id
        WHERE p.dataset_id=? ORDER BY p.participant_id
    """),
    "SV": (("STUDYID", "USUBJID", "VISIT", "SOURCE_VISIT_ID", "SVPLNDTC", "SVSTDTC"), """
        SELECT d.study_id, d.study_id || '-' || v.participant_id, v.visit_code,
               v.visit_id, CAST(v.planned_date AS VARCHAR), CAST(v.actual_date AS VARCHAR)
        FROM visits v JOIN datasets d ON d.dataset_id=v.dataset_id
        WHERE v.dataset_id=? ORDER BY v.participant_id, v.visit_id
    """),
    "VS": (("STUDYID", "USUBJID", "VISIT", "SOURCE_RECORD_ID", "VSTESTCD", "VSORRES", "VSORRESU", "VSDTC"), """
        SELECT d.study_id, d.study_id || '-' || v.participant_id, v.visit_code,
               m.measurement_id, m.test_code, CAST(m.result_value AS VARCHAR),
               m.unit, CAST(v.actual_date AS VARCHAR)
        FROM vital_signs m
        JOIN visits v ON v.dataset_id=m.dataset_id AND v.visit_id=m.visit_id
        JOIN datasets d ON d.dataset_id=m.dataset_id
        WHERE m.dataset_id=? ORDER BY v.participant_id, v.visit_id, m.measurement_id
    """),
    "LB": (("STUDYID", "USUBJID", "VISIT", "SOURCE_RECORD_ID", "LBTESTCD", "LBORRES", "LBORRESU", "LBDTC"), """
        SELECT d.study_id, d.study_id || '-' || v.participant_id, v.visit_code,
               m.measurement_id, m.test_code, CAST(m.result_value AS VARCHAR),
               m.unit, CAST(v.actual_date AS VARCHAR)
        FROM laboratory_results m
        JOIN visits v ON v.dataset_id=m.dataset_id AND v.visit_id=m.visit_id
        JOIN datasets d ON d.dataset_id=m.dataset_id
        WHERE m.dataset_id=? ORDER BY v.participant_id, v.visit_id, m.measurement_id
    """),
    "AE": (("STUDYID", "USUBJID", "SOURCE_EVENT_ID", "AETERM", "AESEV", "AESTDTC", "AEENDTC"), """
        SELECT d.study_id, d.study_id || '-' || a.participant_id, a.event_id,
               a.event_term, a.severity, CAST(a.start_date AS VARCHAR),
               CASE WHEN a.end_date IS NULL THEN '' ELSE CAST(a.end_date AS VARCHAR) END
        FROM adverse_events a JOIN datasets d ON d.dataset_id=a.dataset_id
        WHERE a.dataset_id=? ORDER BY a.participant_id, a.event_id
    """),
}


def export_synthetic_domains(db_path: Path, dataset_id: str, output_dir: Path) -> dict[str, Any]:
    """Write five simplified CSV views, scoped to one selected synthetic dataset."""
    db_path, output_dir = Path(db_path), Path(output_dir)
    if not db_path.is_file():
        raise ExportError(f"Database does not exist: {db_path}")
    if not dataset_id:
        raise ExportError("Choose a dataset identifier")
    con = _db_module().connect(str(db_path.resolve()), read_only=True)
    try:
        source = con.execute(
            "SELECT study_id, synthetic_only FROM datasets WHERE dataset_id=?", [dataset_id]
        ).fetchone()
        if not source:
            raise ExportError("Dataset not found")
        if source[1] is not True:
            raise ExportError("Only synthetic datasets may be exported")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        # Generate all files in a temporary directory before publishing any.
        with tempfile.TemporaryDirectory(prefix="study-export-", dir=output_dir.parent) as tmp:
            staging = Path(tmp)
            counts = {}
            for domain, (columns, sql) in DOMAIN_QUERIES.items():
                result = con.execute(sql, [dataset_id])
                records = result.fetchall()
                counts[domain] = len(records)
                with (staging / f"demo_{domain}.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream, lineterminator="\n")
                    writer.writerow(columns)
                    writer.writerows(records)
            manifest = {
                "dataset_id": dataset_id,
                "study_id": source[0],
                "synthetic_only": True,
                "regulatory_compliant": False,
                "description": "Simplified CDISC-inspired CSV views, NOT compliant SDTM domains.",
                "domains": {domain: {"filename": f"demo_{domain}.csv", "columns": list(cols), "rows": counts[domain]}
                            for domain, (cols, _) in DOMAIN_QUERIES.items()},
            }
            (staging / "mapping_manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            for item in staging.iterdir():
                os.replace(item, output_dir / item.name)
        return {"status": "EXPORTED", "dataset_id": dataset_id,
                "output_dir": str(output_dir), "rows": counts}
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError(f"Could not export synthetic domains: {exc}") from exc
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Export simplified, noncompliant synthetic study views")
    parser.add_argument("--db", type=Path, default=Path("data/challenge.duckdb"))
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports"))
    args = parser.parse_args()
    try:
        result = export_synthetic_domains(args.db, args.dataset_id, args.output_dir)
    except ExportError as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
