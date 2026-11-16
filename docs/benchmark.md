# Controlled data-quality benchmark

The benchmark tests the quality review against a reproducible, deliberately corrupted **synthetic** study dataset. It does not evaluate clinical outcomes, medical diagnosis or compliance with regulatory standards.

## Approach

1. Generate the clean dataset using the study simulator.
2. Copy it to a **different directory** and inject a fixed set of defects without breaking the source table schema or foreign-key relationships.
3. Rebuild the manifest checksums and row counts for the modified CSVs.
4. Write `ground_truth.json`, which records the exact rule, table and record identifier expected for each injected case.
5. Ingest the modified source under its own dataset ID, run the quality engine and compare detected findings against the reference labels.

The baseline CSVs and their manifest remain unchanged. The challenge tool refuses to overwrite an existing output folder.

## Commands

From the repository root with the Python environment activated:

```bash
python -m clinical_trial.generate --output-dir data/generated
python -m clinical_trial.challenge inject --source data/generated --output data/challenge
python -m clinical_trial.verify --input-dir data/challenge
python -m clinical_trial.ingest --input-dir data/challenge --db data/challenge.duckdb
python -m clinical_trial.challenge score --db data/challenge.duckdb --truth data/challenge/ground_truth.json --report-dir data/benchmark
```

`data/benchmark/benchmark.json` contains overall, per-rule and per-case metrics. `detected_issues.csv` contains the findings returned by the quality engine.

The default dataset provides enough events and participants to exercise all 19 quality rule IDs. For a smaller input, the injector requires at least 18 participants and five generated adverse events.

## Reference labels and correlated findings

The fixture contains **19 deliberate cases and 22 expected rule-level findings**. Some single mutations have two valid effects:

- Replacing a scheduled visit with an unknown visit code triggers both V002 (unknown code) and V001 (scheduled visit absent).
- Recording the baseline visit before enrolment triggers V006 (before enrolment) and V005 (outside its allowed window).
- Replacing an expected measurement code with an unknown code triggers M001 (unknown test) and M004 (expected test absent).

These secondary findings are explicitly included in the expected set; they are not counted as false positives. The missing-visit case removes its dependent measurements to preserve relational integrity, and a duplicated visit includes all expected measurements to avoid generating unrelated missing-measurement findings.

Each finding is matched on the tuple `(rule_id, source_table, record_id)`, not on its issue message or severity. Rules are assessed against the configuration checksum recorded when the fixture was created.

## Scoring

- **True positive:** a reference finding returned by the quality engine.
- **False negative:** a reference finding the engine missed.
- **False positive:** a returned finding absent from the fixture's reference labels.

Precision = TP / (TP + FP), recall = TP / (TP + FN), and F1 is their harmonic mean. Undefined ratios are represented as JSON `null`, not silently treated as 100%.

No true-negative universe is defined, so **accuracy and specificity are not reported**. Full agreement on this deterministic fixture shows that the software detects these specific synthetic cases; it is not proof of performance on messy real-world clinical data, other protocols, or new error patterns. The benchmark is deliberately derived from the same published rule specification used by the checker; it is a regression test, not independent validation of clinical correctness.

The fixture must be regenerated after changing rules. The scorer refuses to compare against a different ruleset checksum or an incorrect dataset ID.

## Limits

- The values and visit schedules are invented; the review limits are **not** medical reference ranges.
- The fixture is designed to pass structural ingestion checks. Malformed data types, broken foreign keys and invalid manifests are tested separately by the ingestion suite.
- Injected cases are deterministic rather than a representative sample of real-world error rates.
- Do not upload real patient data to this project.
