# Study-data quality review

The quality review checks **loaded synthetic study records** against rules in
[`configs/quality_rules.json`](../configs/quality_rules.json). It does not change
original records, correct findings automatically or infer medical outcomes.

## Run locally

From the repository root, after generating and ingesting the data:

```bash
python -m clinical_trial.quality --db data/study.duckdb --report data/quality/issues.csv
```

The command prints a JSON summary including the dataset ID, ruleset SHA-256,
issue count and counts by rule and severity. Issues remain accessible in the
`quality_issues` table and in the optional CSV report.

A clean dataset produced by the default synthetic generator is expected to yield
zero findings. That means only that the generator's output matches this
fictional study's configured rules; **it is not evidence of clinical quality**.

If a database contains several ingested datasets, select one explicitly:

```bash
python -m clinical_trial.quality --db data/study.duckdb --dataset-id <dataset-sha256>
```

The `dataset_id` is reported by the ingestion command, or with
`python -m clinical_trial.ingest --db data/study.duckdb --status`.

## Checks

All findings have a stable SHA-256 issue ID, rule ID, severity (`ERROR` or
`WARNING`), source table, record identifier, optional participant/visit
references and observed/expected information.

| Rule | Level | Meaning |
| --- | --- | --- |
| P001 | WARNING | Age outside fictional study eligibility bounds |
| P002 | ERROR | Unknown treatment arm |
| P003 | WARNING | Unknown recorded sex code |
| V001 | ERROR | Expected visit missing for a participant |
| V002 | ERROR | Unrecognized visit code |
| V003 | ERROR | Duplicate visit code for a participant |
| V004 | ERROR | Planned visit date differs from configured schedule |
| V005 | WARNING | Actual visit date outside configured visit window |
| V006 | ERROR | Visit date earlier than participant enrolment |
| M001 | ERROR | Measurement code not included in the study dictionary |
| M002 | ERROR | Measurement unit differs from the configured unit |
| M003 | WARNING | Measurement outside **synthetic study review bounds** |
| M004 | ERROR | Expected measurement missing for a recorded visit |
| M005 | ERROR | Duplicate measurement code for a visit |
| A001 | ERROR | Adverse-event end date earlier than start date |
| A002 | ERROR | Adverse-event start date earlier than enrolment |
| A003 | WARNING | Unknown adverse-event severity code |
| A004 | WARNING | Unknown adverse-event term |
| A005 | WARNING | Adverse-event start beyond study follow-up window |

These rules do not check serious adverse-event causality, medical validity,
CDISC compliance, missing source-file fields or regulatory requirements.
Structural/type errors are rejected by the separate ingestion preflight.

## Data model and reruns

- `quality_runs` records the rule-config checksum and issue count for each
  evaluation; prior run summaries are retained.
- `quality_issues` stores **current** findings by dataset, with references to
  the latest evaluation that produced them.
- Re-running the evaluation atomically replaces a dataset's current issues,
  without duplicating entries or changing the study data.
- Findings from distinct ingested datasets stay separate even where they use
  the same simulated participant and visit IDs.
- A failed evaluation rolls back the issue/history updates.
- Issue identifiers depend on dataset, rule, source table and record ID;
  altering the *value* of the same offending record does not change its ID.

The review bounds are intentionally independent of the generator and are
stored in a second JSON configuration so assumptions can be inspected and
versioned. They are **not medical reference ranges**.

## Testing and limits

Tests deliberately modify a disposable DuckDB dataset (not patient data) to
verify detection of incompatible units, out-of-window visits, missing and
duplicate observations, unexpected categories and adverse-event chronology.

This is an educational data engineering project using entirely synthetic
records. It is not a clinical trial system, GCP validation package, electronic
data capture application or substitute for clinician review. It must not be
used with identifiable health data.
