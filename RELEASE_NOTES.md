# Synthetic clinical study data platform — v0.7.0

A local, reproducible demonstration of clinical-study data ingestion and quality review using fully synthetic records.

- Deterministic four-site study generator with checksummed CSV manifests.
- Transactional DuckDB ingestion, provenance tracking and duplicate-load protection.
- Nineteen configurable data-quality checks and record-level issue reports.
- Controlled error injection with 19 cases and 22 expected rule-level findings.
- Streamlit dashboard for site, visit and rule summaries; local reviewer decisions with retained event history.
- Five simplified CDISC-inspired synthetic CSV exports with an explicit noncompliance statement.
- Clean-room verification (`python scripts/verify_release.py`) and GitHub Actions tests on Python 3.11 and 3.12.

The controlled benchmark is a deterministic regression test. Perfect agreement on its 22 expected findings is **not** evidence of clinical accuracy, real-world fault detection performance or regulatory readiness. The project contains no real patient data, does not authenticate reviewers and is not for clinical use.
