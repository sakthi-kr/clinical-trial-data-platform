-- Phase 2: source provenance + typed relational tables. All records are synthetic.
-- A dataset is identified by the SHA-256 digest of the exact manifest bytes.
-- Composite keys allow different generated batches to reuse participant IDs.

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id VARCHAR PRIMARY KEY,
    study_id VARCHAR NOT NULL,
    manifest_sha256 VARCHAR NOT NULL,
    source_directory VARCHAR NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    synthetic_only BOOLEAN NOT NULL CHECK (synthetic_only = TRUE)
);

CREATE TABLE IF NOT EXISTS ingestion_events (
    event_id VARCHAR PRIMARY KEY,
    dataset_id VARCHAR,
    event_type VARCHAR NOT NULL CHECK (event_type IN ('LOADED','SKIPPED','FAILED')),
    event_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details VARCHAR
);

CREATE TABLE IF NOT EXISTS source_files (
    dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id),
    table_name VARCHAR NOT NULL,
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    source_rows BIGINT NOT NULL,
    PRIMARY KEY (dataset_id, table_name)
);

CREATE TABLE IF NOT EXISTS sites (
    dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id),
    site_id VARCHAR NOT NULL,
    site_name VARCHAR NOT NULL,
    PRIMARY KEY (dataset_id, site_id)
);
CREATE TABLE IF NOT EXISTS participants (
    dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id),
    participant_id VARCHAR NOT NULL,
    site_id VARCHAR NOT NULL,
    study_group VARCHAR NOT NULL,
    age_years INTEGER NOT NULL,
    recorded_sex VARCHAR NOT NULL,
    enrollment_date DATE NOT NULL,
    PRIMARY KEY (dataset_id, participant_id),
    FOREIGN KEY (dataset_id, site_id) REFERENCES sites(dataset_id, site_id)
);
CREATE TABLE IF NOT EXISTS visits (
    dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id),
    visit_id VARCHAR NOT NULL,
    participant_id VARCHAR NOT NULL,
    visit_code VARCHAR NOT NULL,
    planned_date DATE NOT NULL,
    actual_date DATE NOT NULL,
    PRIMARY KEY (dataset_id, visit_id),
    FOREIGN KEY (dataset_id, participant_id) REFERENCES participants(dataset_id, participant_id)
);
CREATE TABLE IF NOT EXISTS vital_signs (
    dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id),
    measurement_id VARCHAR NOT NULL,
    visit_id VARCHAR NOT NULL,
    test_code VARCHAR NOT NULL,
    result_value DECIMAL(18,6) NOT NULL,
    unit VARCHAR NOT NULL,
    PRIMARY KEY (dataset_id, measurement_id),
    FOREIGN KEY (dataset_id, visit_id) REFERENCES visits(dataset_id, visit_id)
);
CREATE TABLE IF NOT EXISTS laboratory_results (
    dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id),
    measurement_id VARCHAR NOT NULL,
    visit_id VARCHAR NOT NULL,
    test_code VARCHAR NOT NULL,
    result_value DECIMAL(18,6) NOT NULL,
    unit VARCHAR NOT NULL,
    PRIMARY KEY (dataset_id, measurement_id),
    FOREIGN KEY (dataset_id, visit_id) REFERENCES visits(dataset_id, visit_id)
);
CREATE TABLE IF NOT EXISTS adverse_events (
    dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id),
    event_id VARCHAR NOT NULL,
    participant_id VARCHAR NOT NULL,
    event_term VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    PRIMARY KEY (dataset_id, event_id),
    FOREIGN KEY (dataset_id, participant_id) REFERENCES participants(dataset_id, participant_id)
);
