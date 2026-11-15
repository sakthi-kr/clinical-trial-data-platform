# Clinical Trial Data Quality & Validation Platform

**Status: Phase 1 implemented; Phase 2 ingestion implementation ready for local verification.** The repository contains a reproducible fictional-study generator, source-manifest verification, a typed DuckDB schema and a transactional ingestion pipeline. The Phase 3 clinical validation engine and dashboard are **not implemented yet**.

> **Synthetic data only.** This is an educational software/data-engineering demonstration, not a real clinical trial or medical assessment, and does not establish CDISC SDTM, GCP, privacy-law or regulatory compliance. Never add identifiable patient data.

## Problem

Clinical-study teams need repeatable, traceable ingestion before they can inspect data-quality issues. This repository generates a fictional four-site, 12-week study and loads the generated records into a typed relational database, with source-file checksums, automatic rejection of structurally malformed batches and detailed ingestion logs.

## What is currently implemented

**Phase 1:** synthetic study generator, six CSV source tables, manifest SHA-256 checksums, fixed seed, reproducibility tests.

**Phase 2:** six typed DuckDB tables, source-file provenance, participant/visit foreign keys, strong parsing of dates and numeric data, dataset-based idempotency, transactional rollback on failure, failed/skipped/success event history, per-table count reconciliation and database integration tests.

This phase checks **structural validity**, not clinical acceptability of recorded values. More advanced quality rules and issues will arrive in Phase 3.

## Requirements and cost

- Windows 11 with **Python 3.11** recommended; also tested in CI on 3.12 once installed in GitHub Actions.
- CPU only. No GPU, cloud, API key, Docker or Java.
- **DuckDB** (free, local) is the only runtime dependency. **pytest** is the development/test dependency.
- Local free disk space: a few hundred MB for the environment and sample database are normally sufficient; 5 GB provides comfortable room for later phases.

## Windows PowerShell quick start

Extract this repository to a new folder and open the `clinical-trial-data-platform` directory in PyCharm. In its terminal:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# Phase 1 — generate and verify
.\.venv\Scripts\python.exe -m clinical_trial.generate --output-dir data/generated
.\.venv\Scripts\python.exe -m clinical_trial.verify --input-dir data/generated

# Phase 2 — populate a local DuckDB database
.\.venv\Scripts\python.exe -m clinical_trial.ingest --input-dir data/generated --db data/study.duckdb

# Repeat the same import: it should show SKIPPED, not duplicate data
.\.venv\Scripts\python.exe -m clinical_trial.ingest --input-dir data/generated --db data/study.duckdb

# Inspect counts and ingestion events
.\.venv\Scripts\python.exe -m clinical_trial.ingest --db data/study.duckdb --status

# Isolated end-to-end smoke test (temporary files, then clean up)
.\.venv\Scripts\python.exe -m clinical_trial.smoke

# Tests (includes the existing Phase 1 tests)
.\.venv\Scripts\python.exe -m pytest -q
```

No PowerShell execution-policy changes are required because these commands call the venv interpreter directly. The `.venv` directory and `.duckdb` database remain local and are ignored by Git.

**Upgrading from Phase 1:** This ZIP contains the same generator and protocol as Phase 1 but now adds DuckDB. You can either regenerate `data/generated` in the new folder with the command above (simplest), or copy your existing `data/generated` folder across. Do not overwrite your original project until Phase 2 passes locally.

## Expected Phase 2 output (500 participants)

| Table | Rows |
| --- | ---: |
| sites | 4 |
| participants | 500 |
| visits | 2,000 |
| vital_signs | 4,000 |
| laboratory_results | 8,000 |
| adverse_events | Deterministic for seed in protocol (49 for default baseline) |

On the first import, look for `"status": "LOADED"` and reconciled counts. On the second, look for `"status": "SKIPPED"`. The database uses the manifest's exact SHA-256 as `dataset_id`; different synthetic batches can coexist without mixing participant IDs.

## Repository layout

```text
clinical-trial-data-platform/
├── configs/study_protocol.json
├── src/clinical_trial/
│   ├── __init__.py
│   ├── generate.py               # Phase 1 deterministic generator
│   ├── verify.py                 # Phase 1 file integrity checks
│   ├── schema.py                 # CSV contracts
│   ├── validate_source.py        # Phase 2 strict structural preflight
│   ├── ingest.py                 # Phase 2 DuckDB CLI and transactional loader
│   ├── smoke.py                  # Independent end-to-end smoke test
│   └── db_schema.sql             # Typed relational schema and lineage tables
├── tests/
│   ├── test_generate.py
│   ├── test_validate_source.py
│   └── test_ingest.py            # Requires DuckDB; skipped if missing
├── data/example/                 # Small committed synthetic CSV example
├── data/generated/               # Generated locally; ignored by git
├── docs/
│   ├── data_dictionary.md
│   ├── study_protocol.md
│   ├── phase2_ingestion.md
│   └── decisions.md
├── .github/workflows/tests.yml
├── PLAN.md
└── pyproject.toml
```

## Project documentation

- [Protocol and caveats](docs/study_protocol.md)
- [Data dictionary](docs/data_dictionary.md)
- [Phase 2 database and provenance design](docs/phase2_ingestion.md)
- [Roadmap and phase acceptance tests](PLAN.md)
- [Design decisions](docs/decisions.md)

## Roadmap

**Phase 3:** clinical rule engine and traceable issue reports. **Phase 4:** controlled error injection, known-ground-truth evaluation and honest precision/recall. **Phase 5:** Streamlit quality dashboard. **Phase 6:** reviewed issue lifecycle and CDISC-inspired exports. **Phase 7:** reproducibility review and tagged release.

## External concepts

- [DuckDB SQL transaction reference](https://duckdb.org/docs/current/sql/statements/transactions)
- [CDISC study data concepts](https://www.cdisc.org/standards/foundational/sdtm), for reference only
- [Synthea](https://github.com/synthetichealth/synthea), an optional future source of *synthetic EHR* context, not used by this generator
