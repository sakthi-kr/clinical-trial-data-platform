# Review annotations and synthetic domain exports

The application stores review annotations separately from clinical study records. A reviewer can classify a **current data-quality finding** as `IN_REVIEW`, `CONFIRMED`, or `DISMISSED`, with an accompanying note and a local reviewer label. These statuses describe a human review decision; **none of them means that a clinical record was corrected**.

## Review actions

Open the [dashboard](dashboard.md), select a finding in the Findings tab, and fill out its review form. The page shows current review statuses and a separate review-activity history.

The backend can also be called directly:

```bash
python -m clinical_trial.review \
  --db data/challenge.duckdb \
  --dataset-id <dataset-sha256> \
  --issue-id <issue-sha256> \
  --status IN_REVIEW \
  --reviewer "demo analyst" \
  --note "Checking the simulated source record" \
  --expected-version 0
```

Use the dashboard or the existing quality-issue CSV report to find dataset and issue IDs. `expected-version` prevents saving an out-of-date form over a newer review action.

Every successful action appends one row to `issue_review_events`; an action does **not** overwrite previous review events, evaluation summaries or synthetic measurements. Reviews remain associated with the original dataset and the full current finding signature (rule, severity, source, record, observed and expected values). If a finding disappears or its content changes on re-evaluation, previous review history stays visible, but the old classification is not automatically attached to the new finding. An unchanged finding retains its classification through re-evaluation.

The review module is a **single-machine demonstration without authentication, access controls, electronic signatures or tamper-proof audit storage**. Reviewer labels are self-reported. Never enter real patient information or personal data in notes. The underlying DuckDB database can be edited with external tools; this is not a regulatory audit system.

## CDISC-inspired synthetic export

The project can generate five explicitly noncompliant, simplified demonstration tables for a selected synthetic dataset:

```bash
python -m clinical_trial.export_domains \
  --db data/challenge.duckdb \
  --dataset-id <dataset-sha256> \
  --output-dir data/exports
```

| Output | Origin | Content |
| --- | --- | --- |
| `demo_DM.csv` | participants | One row per synthetic participant; age, arm and enrollment |
| `demo_SV.csv` | visits | Scheduled and actual study visits |
| `demo_VS.csv` | vital_signs and visits | Fictional vital-sign measurements and units |
| `demo_LB.csv` | laboratory_results and visits | Fictional lab results and units |
| `demo_AE.csv` | adverse_events | Fictional events and dates |

`mapping_manifest.json` states `synthetic_only: true` and `regulatory_compliant: false`, and lists the output field names and row counts. Some identifiers (for example `SOURCE_RECORD_ID` and `SVPLNDTC`) are educational provenance fields, not submission-standard definitions. No CDISC validation, controlled-terminology reconciliation, derivation of regulatory variables, regulatory metadata package or submission-readiness check is performed. **Do not describe these files as CDISC SDTM-compliant.**

All exported data are synthetic. Output folders under `data/exports/` are excluded from version control. Export and review commands do not change the original clinical-study records.
