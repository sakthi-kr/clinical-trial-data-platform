"""One-command isolated Phase-2 smoke check (requires installed DuckDB)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from clinical_trial.generate import generate_dataset
from clinical_trial.ingest import load_dataset, summarize
from clinical_trial.verify import verify_manifest


def main() -> None:
    config = Path("configs/study_protocol.json")
    if not config.is_file():
        raise SystemExit("Run the smoke command from the repository root directory")
    with tempfile.TemporaryDirectory(prefix="clinical_phase2_") as work:
        root = Path(work)
        input_dir = root / "csv"
        db = root / "study.duckdb"
        manifest = generate_dataset(config, input_dir, participant_count=24, seed=2026)
        errors = verify_manifest(input_dir)
        if errors:
            raise RuntimeError(f"Unexpected source failures: {errors}")
        result1 = load_dataset(input_dir, db)
        result2 = load_dataset(input_dir, db)
        summary = summarize(db)
        assert result1["status"] == "LOADED", result1
        assert result2["status"] == "SKIPPED", result2
        assert len(summary["datasets"]) == 1, summary
        assert summary["datasets"][0]["counts"] == result1["counts"], summary
        assert result1["counts"] == {name: table["rows"] for name, table in manifest["tables"].items()}
        event_types = [event["event_type"] for event in summary["events"]]
        assert sorted(event_types) == ["LOADED", "SKIPPED"], event_types
        print(json.dumps({"smoke_test": "PASS", "first_import": "LOADED",
                          "repeat_import": "SKIPPED", "row_counts": result1["counts"]}, indent=2))


if __name__ == "__main__":
    main()
