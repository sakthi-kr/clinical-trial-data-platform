"""Dataset isolation and determinism of explicitly noncompliant synthetic exports."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from clinical_trial.generate import generate_dataset
from clinical_trial.ingest import load_dataset
from clinical_trial.export_domains import ExportError, DOMAIN_QUERIES, export_synthetic_domains

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/study_protocol.json"


def _study(tmp_path, n=15, seed=20, db=None):
    source = tmp_path / f"src_{seed}"
    generate_dataset(PROTOCOL, source, participant_count=n, seed=seed)
    db = db or tmp_path / "data.duckdb"
    result = load_dataset(source, db)
    return db, result["dataset_id"], result["counts"]


def test_all_five_domains_export_correct_counts_and_metadata(tmp_path):
    db, dataset, counts = _study(tmp_path)
    out = tmp_path / "out"
    before = db.read_bytes()
    result = export_synthetic_domains(db, dataset, out)
    assert result["status"] == "EXPORTED"
    assert result["rows"] == {"DM": counts["participants"], "SV": counts["visits"],
                              "VS": counts["vital_signs"], "LB": counts["laboratory_results"],
                              "AE": counts["adverse_events"]}
    manifest = json.loads((out / "mapping_manifest.json").read_text())
    assert manifest["synthetic_only"] is True
    assert manifest["regulatory_compliant"] is False
    assert set(manifest["domains"]) == set(DOMAIN_QUERIES)
    for domain, (columns, _) in DOMAIN_QUERIES.items():
        with (out / f"demo_{domain}.csv").open(encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            assert tuple(reader.fieldnames) == columns
            rows = list(reader)
        assert len(rows) == result["rows"][domain]
        assert all(x["USUBJID"].startswith("SYN-CTQA-001-") for x in rows)
    assert db.read_bytes() == before


def test_exports_are_dataset_scoped_and_reproducible(tmp_path):
    db, a, _ = _study(tmp_path, n=15, seed=21)
    _, b, _ = _study(tmp_path, n=22, seed=22, db=db)
    first_dir = tmp_path / "a1"
    second_dir = tmp_path / "a2"
    export_synthetic_domains(db, a, first_dir)
    export_synthetic_domains(db, a, second_dir)
    for name in ("DM", "SV", "VS", "LB", "AE"):
        assert (first_dir / f"demo_{name}.csv").read_bytes() == (second_dir / f"demo_{name}.csv").read_bytes()
    export_synthetic_domains(db, b, tmp_path / "b")
    assert len((tmp_path / "b" / "demo_DM.csv").read_text().splitlines()) == 23
    assert len((first_dir / "demo_DM.csv").read_text().splitlines()) == 16


def test_unknown_dataset_does_not_create_output(tmp_path):
    db, _, _ = _study(tmp_path)
    output = tmp_path / "not-created"
    with pytest.raises(ExportError, match="not found"):
        export_synthetic_domains(db, "unknown", output)
    assert not output.exists()
