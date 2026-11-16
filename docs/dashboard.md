# Interactive quality dashboard

The dashboard provides a **read-only** view over the local DuckDB study database.
It never imports datasets, runs quality checks, changes records or adjudicates findings.
All records are simulated. The review bounds are synthetic and are **not medical reference ranges**.

## Install and launch

```bash
python -m pip install -e '.[dev,dashboard]'
python -m streamlit run src/clinical_trial/dashboard.py
```

Streamlit prints a local URL (usually `http://localhost:8501`). It opens in your browser and runs on your machine. No Streamlit Cloud account or API key is required.

If you have already generated and ingested the original synthetic dataset, evaluate it:

```bash
python -m clinical_trial.quality --db data/study.duckdb
```

The default synthetic dataset is designed to pass the configured checks, so a zero-finding view is expected.
To explore nonempty charts, create and evaluate the controlled synthetic challenge fixture:

```bash
python -m clinical_trial.challenge inject --source data/generated --output data/challenge
python -m clinical_trial.ingest --input-dir data/challenge --db data/challenge.duckdb
python -m clinical_trial.challenge score \
  --db data/challenge.duckdb \
  --truth data/challenge/ground_truth.json \
  --report-dir data/benchmark
```

The dashboard selects `data/challenge.duckdb` when present; otherwise it defaults to `data/study.duckdb`.
Change the path in the sidebar if required.

## How to read the results

- **Overview** gives overall source counts, current finding counts and distributions by site, rule, visit and severity. Site, visit, severity, rule and participant filters apply to the charts and findings table, not to the headline dataset totals.
- **Findings** displays the current issues and allows downloading the **filtered** issue metadata as CSV. No raw participant or measurement tables are exported.
- **Evaluation history** lists recorded evaluation runs and ruleset checksums. History stores summaries, not historical issue-level snapshots.
- **Data source** can select between multiple imported synthetic batches; rows are isolated by `dataset_id`.

The app does not automatically refresh the database. Streamlit reruns queries when you interact with a control or refresh the page; when running ingestion or quality review externally, refresh the browser to view updated results.

## Important limitations

A finding reflects one configured rule on a synthetic record. A clean review means only that these synthetic records passed the configured checks, **not** that a real study is clinically valid. Counts in issue charts represent rule-level findings, not unique affected patients. The dashboard has no authentication or support for real patient information, and must not be deployed with identifiable medical records or used for clinical or regulated decisions.
