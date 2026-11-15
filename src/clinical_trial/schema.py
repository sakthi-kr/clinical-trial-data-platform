"""Canonical phase-1 CSV table column contracts."""

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "sites": ("site_id", "site_name"),
    "participants": (
        "participant_id", "site_id", "study_group", "age_years", "recorded_sex", "enrollment_date"
    ),
    "visits": (
        "visit_id", "participant_id", "visit_code", "planned_date", "actual_date"
    ),
    "vital_signs": (
        "measurement_id", "visit_id", "test_code", "result_value", "unit"
    ),
    "laboratory_results": (
        "measurement_id", "visit_id", "test_code", "result_value", "unit"
    ),
    "adverse_events": (
        "event_id", "participant_id", "event_term", "severity", "start_date", "end_date"
    ),
}
