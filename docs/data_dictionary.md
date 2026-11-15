# Data dictionary — Phase 1

All identifiers below are **synthetic**, and tables are stored as UTF-8 CSV. Date columns use `YYYY-MM-DD`. Numeric values are serialized as strings in CSV and will be explicitly typed during Phase 2 ingestion. The CSV schema is enforced by `src/clinical_trial/schema.py`.

## Relationships and grain

```mermaid
erDiagram
    SITES ||--o{ PARTICIPANTS : contains
    PARTICIPANTS ||--|{ VISITS : attends
    VISITS ||--|{ VITAL_SIGNS : records
    VISITS ||--|{ LABORATORY_RESULTS : records
    PARTICIPANTS ||--o{ ADVERSE_EVENTS : has
```

### `sites` — one row per fictional site

| Field | Type | Constraint |
|---|---|---|
| `site_id` | text | primary key, e.g. S01 |
| `site_name` | text | required; fictional only |

### `participants` — one row per participant

| Field | Type | Constraint |
|---|---|---|
| `participant_id` | text | primary key, e.g. P000001 |
| `site_id` | text | required; references sites.site_id |
| `study_group` | text | ARM_A or ARM_B, arbitrary labels |
| `age_years` | integer | generated ages 18–80 |
| `recorded_sex` | text | F / M / U in this simplified fictional source |
| `enrollment_date` | ISO date | required |

### `visits` — one row per participant and scheduled visit

| Field | Type | Constraint |
|---|---|---|
| `visit_id` | text | primary key |
| `participant_id` | text | references participants.participant_id |
| `visit_code` | text | BASELINE / WEEK04 / WEEK08 / WEEK12 |
| `planned_date` | ISO date | enrollment date + protocol day |
| `actual_date` | ISO date | within fictional protocol window in clean baseline |

Unique business key: (`participant_id`, `visit_code`). In Phase 4 this may be corrupted for duplicate-detection tests.

### `vital_signs` — one row per visit and measurement type

| Field | Type | Constraint |
|---|---|---|
| `measurement_id` | text | primary key |
| `visit_id` | text | references visits.visit_id |
| `test_code` | text | SBP / HR |
| `result_value` | decimal | synthetic reading |
| `unit` | text | mmHg for SBP; beats/min for HR |

Unique clean-data business key: (`visit_id`, `test_code`).

### `laboratory_results` — one row per visit and laboratory test

| Field | Type | Constraint |
|---|---|---|
| `measurement_id` | text | primary key |
| `visit_id` | text | references visits.visit_id |
| `test_code` | text | HGB / GLU / CREAT / SOD |
| `result_value` | decimal | synthetic reading |
| `unit` | text | g/dL / mg/dL / mg/dL / mmol/L respectively |

Unique clean-data business key: (`visit_id`, `test_code`).

### `adverse_events` — zero or more events per participant

| Field | Type | Constraint |
|---|---|---|
| `event_id` | text | primary key |
| `participant_id` | text | references participants.participant_id |
| `event_term` | text | HEADACHE / FATIGUE / NAUSEA / DIZZINESS |
| `severity` | text | MILD / MODERATE; simulated only |
| `start_date` | ISO date | on/after enrollment |
| `end_date` | optional ISO date | empty for simulated ongoing events; otherwise on/after start |

## Planned future technical tables

Phase 2 onward adds `ingestion_runs`, `source_files`, `quality_rules`, `quality_issues`, `injected_errors`, and `audit_events`, all documented when implemented.
