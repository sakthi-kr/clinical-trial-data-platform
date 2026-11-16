"""Minimal browser-independent smoke tests for the Streamlit dashboard."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest

from clinical_trial.generate import generate_dataset
from clinical_trial.ingest import load_dataset
from clinical_trial.quality import evaluate_dataset

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/clinical_trial/dashboard.py"
PROTOCOL = ROOT / "configs/study_protocol.json"
RULES = ROOT / "configs/quality_rules.json"


def test_dashboard_with_clean_review(tmp_path: Path, monkeypatch):
    source = tmp_path / "generated"
    db = tmp_path / "data" / "study.duckdb"
    generate_dataset(PROTOCOL, source, participant_count=16, seed=19)
    load_dataset(source, db)
    evaluate_dataset(db, RULES)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert len(app.exception) == 0
    assert len(app.metric) == 5


def test_dashboard_missing_database_explains_setup(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert len(app.exception) == 0
    assert len(app.info) >= 1
