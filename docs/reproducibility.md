# Reproducing the synthetic study demonstration

Use Python 3.11 or 3.12 in a virtual environment. No database server, cloud account or clinical data is required. All test records are synthetic.

Install from the repository root:

```bash
python -m pip install -e '.[dev,dashboard]'
python -m pip check
python -m pytest -q -rs
python scripts/verify_release.py
```

`verify_release.py` runs in a fresh temporary directory. It checks generation and source manifests, imports a clean batch, confirms zero quality findings for that batch, injects controlled defects into a separate copy, ingests and scores the challenge, saves two review events, produces five simplified synthetic exports, and checks that the clean input's manifest is unchanged. Temporary data is removed automatically.

The current deterministic reference fixture contains 19 injected fault cases and 22 expected rule-level findings. On tested software, the benchmark should return 22 true positives, zero false positives and zero false negatives. This is agreement with known synthetic reference labels, **not** measured sensitivity or specificity on clinical data.

To inspect the application interactively, generate the default clean dataset and use the commands in the [README](../README.md). Run the synthetic challenge to populate the dashboard with findings:

```bash
python -m clinical_trial.generate --output-dir data/generated
python -m clinical_trial.challenge inject --source data/generated --output data/challenge
python -m clinical_trial.ingest --input-dir data/challenge --db data/challenge.duckdb
python -m clinical_trial.challenge score --db data/challenge.duckdb --truth data/challenge/ground_truth.json --report-dir data/benchmark
python -m streamlit run src/clinical_trial/dashboard.py
```

The dashboard opens a local URL (usually `http://localhost:8501`). Use **Ctrl+C** in the terminal to stop the server. In the Findings tab, assign a fictional reviewer label and a note to an issue. The Review activity tab should show the saved event and retain earlier decisions after subsequent updates.

## What this does not reproduce

The demo has no authentic clinical datasets, no authenticated reviewers, no electronic signatures or tamper-resistant regulatory audit trail. Its simulated visit windows and measurement review bounds are project rules, not clinical reference ranges. The simplified CDISC-inspired CSV outputs are **not SDTM compliant** and are unsuitable for regulatory submissions.

The Python package is installed from source rather than from a published package index. Dependency ranges are defined in `pyproject.toml`; CI tests Python 3.11 and 3.12 on Ubuntu. Windows execution has also been checked manually using Git Bash.
