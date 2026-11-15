# Build phases and acceptance gates

## Phase 1 — Clean synthetic study foundation (complete)

- Fictional protocol and six source CSV tables with documented relationships.
- Seeded generator and reproducible CSV manifest with SHA-256 hashes.
- Positive and negative tests for manifest integrity and source structure.

**Gate:** phase-1 tests pass and the user has run the generator successfully on Windows.

## Phase 2 — Typed relational ingestion (implementation prepared; local DB verification pending)

- Strict preflight: check per-file hashes/counts and source type/reference contracts.
- Create DuckDB schema with typed fields and composite dataset-scoped keys.
- Insert source manifest, six file lineage records, and six relational tables within a transaction.
- Fail a batch atomically if SQL loading or reconciliation fails; log `FAILED` after rollback.
- Skip identical manifest batches while recording a `SKIPPED` ingestion event.
- Preserve different synthetic runs under different dataset IDs.
- Provide database summary CLI, documentation, negative tests and CI.

**Gate:** DuckDB integration tests pass with a real local DuckDB dependency, including rollback and idempotency; all counts match. A failed batch is **rejected as a whole** (not quarantined row-by-row).

## Phase 3 — Clinical data-quality rule engine

1. Versioned rules: rule ID, severity, target table, fields and description.
2. Detect missing data, inconsistent visit dates, unexpected units, suspicious values and cross-table clinical inconsistencies.
3. Distinguish invalid data from medically unusual yet possible measurements.
4. Write traceable issue reports with record IDs and source-file hashes.

**Gate:** at least 10 independently tested rules with correct positives/negatives.

## Phase 4 — Controlled fault injection and evaluation

1. Inject deliberate errors and save record-level ground truth.
2. Calculate detection precision/recall, false positives and per-rule coverage.
3. Document interactions, misses and false alarms.

**Gate:** reproducible results, no invented performance metrics.

## Phase 5 — Interactive QA dashboard

1. Streamlit filters, counts and issue drill-downs by site, participant and visit.
2. Export CSV/JSON issues and reconcile dashboard statistics to SQL.

**Gate:** dashboard metrics agree with database queries in tests.

## Phase 6 — Domain extensions

1. Rule-review lifecycle with audit events.
2. CDISC-inspired simplified export mapping (not standards certification).
3. Optional Synthea context as separate synthetic EHR enrichment.

## Phase 7 — Portfolio release

1. End-to-end reproducibility and Windows smoke test.
2. Architecture/schema diagrams, failure analysis, screenshots and CI.
3. Tag a release after independently measured metrics are available.
