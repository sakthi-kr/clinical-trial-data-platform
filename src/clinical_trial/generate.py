"""Deterministic generation of a wholly fictional clinical study.

No patient data is loaded. All records, sites, study groups and adverse
 events are invented and are not suitable for clinical or regulatory use.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from clinical_trial import __version__
from clinical_trial.schema import TABLE_COLUMNS


def _iso(day: date) -> str:
    return day.isoformat()


def _simulated_result(rng: random.Random, spec: dict[str, Any]) -> str:
    """Sample only within a synthetic simulation range, not a medical range."""
    value = rng.gauss(float(spec["mean"]), float(spec["std_dev"]))
    value = max(float(spec["simulation_min"]), min(float(spec["simulation_max"]), value))
    places = int(spec["round_digits"])
    if places == 0:
        return str(int(round(value)))
    return f"{value:.{places}f}"


def _validate_protocol(config: dict[str, Any], participant_count: int) -> None:
    if config.get("synthetic_only") is not True:
        raise ValueError("The project only supports explicitly synthetic data.")
    if participant_count <= 0:
        raise ValueError("participant_count must be a positive integer")
    if len(config["sites"]) < 1:
        raise ValueError("At least one fictional site is needed")
    if len(config["groups"]) != 2:
        raise ValueError("Exactly two fictional study groups are expected")
    if not config["visits"] or config["visits"][0]["day"] != 0:
        raise ValueError("Baseline on day 0 is required")
    days = [int(v["day"]) for v in config["visits"]]
    if sorted(set(days)) != days:
        raise ValueError("Visit days must be unique and increasing")
    if len({s["site_id"] for s in config["sites"]}) != len(config["sites"]):
        raise ValueError("Site identifiers must be unique")
    if not 0 <= float(config["adverse_event_probability"]) <= 1:
        raise ValueError("adverse_event_probability must lie between 0 and 1")


def _write_table(path: Path, columns: tuple[str, ...], rows: list[dict]) -> str:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_dataset(
    config_path: Path,
    output_dir: Path,
    *,
    participant_count: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate all six CSVs and a reproducible manifest; return the manifest."""
    config_path = Path(config_path)
    output_dir = Path(output_dir)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    n = int(participant_count if participant_count is not None else config["participant_count"])
    _validate_protocol(config, n)
    effective_seed = int(seed if seed is not None else config["seed"])
    rng = random.Random(effective_seed)
    start = date.fromisoformat(config["study_start_date"])
    visit_specs = config["visits"]
    last_visit_day = int(visit_specs[-1]["day"])
    jitter = min(int(config["visit_window_days"]), 2)

    tables: dict[str, list[dict]] = {name: [] for name in TABLE_COLUMNS}
    tables["sites"] = [dict(s) for s in config["sites"]]
    v_counter = lab_counter = vital_counter = ae_counter = 0

    for i in range(1, n + 1):
        participant_id = f"P{i:06d}"
        site_id = config["sites"][(i - 1) % len(config["sites"])]["site_id"]
        enrollment = start + timedelta(days=rng.randrange(int(config["enrollment_spread_days"]) + 1))
        tables["participants"].append(
            {
                "participant_id": participant_id,
                "site_id": site_id,
                "study_group": rng.choice(config["groups"]),
                "age_years": rng.randint(18, 80),
                "recorded_sex": rng.choice(["F", "M", "U"]),
                "enrollment_date": _iso(enrollment),
            }
        )

        for visit in visit_specs:
            visit_day = int(visit["day"])
            scheduled = enrollment + timedelta(days=visit_day)
            deviation = 0 if visit_day == 0 else rng.randint(-jitter, jitter)
            actual = scheduled + timedelta(days=deviation)
            v_counter += 1
            visit_id = f"V{v_counter:07d}"
            tables["visits"].append(
                {
                    "visit_id": visit_id,
                    "participant_id": participant_id,
                    "visit_code": visit["visit_code"],
                    "planned_date": _iso(scheduled),
                    "actual_date": _iso(actual),
                }
            )
            for spec in config["vital_measurements"]:
                vital_counter += 1
                tables["vital_signs"].append(
                    {
                        "measurement_id": f"VS{vital_counter:08d}",
                        "visit_id": visit_id,
                        "test_code": spec["test_code"],
                        "result_value": _simulated_result(rng, spec),
                        "unit": spec["unit"],
                    }
                )
            for spec in config["laboratory_measurements"]:
                lab_counter += 1
                tables["laboratory_results"].append(
                    {
                        "measurement_id": f"LB{lab_counter:08d}",
                        "visit_id": visit_id,
                        "test_code": spec["test_code"],
                        "result_value": _simulated_result(rng, spec),
                        "unit": spec["unit"],
                    }
                )

        # Independent fictional events: do not interpret differences by group as
        # efficacy or safety findings. They have no simulated causal mechanism.
        if rng.random() < float(config["adverse_event_probability"]):
            ae_counter += 1
            day = rng.randint(1, last_visit_day - 1)
            event_start = enrollment + timedelta(days=day)
            ongoing = rng.random() < 0.15
            event_end = (
                "" if ongoing else _iso(enrollment + timedelta(days=min(last_visit_day, day + rng.randint(1, 9))))
            )
            tables["adverse_events"].append(
                {
                    "event_id": f"AE{ae_counter:07d}",
                    "participant_id": participant_id,
                    "event_term": rng.choice(config["adverse_event_terms"]),
                    "severity": rng.choice(["MILD", "MODERATE"]),
                    "start_date": _iso(event_start),
                    "end_date": event_end,
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "study_id": config["study_id"],
        "generator_version": __version__,
        "synthetic_only": True,
        "seed": effective_seed,
        "participants_requested": n,
        "protocol_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "tables": {},
    }
    for name, columns in TABLE_COLUMNS.items():
        digest = _write_table(output_dir / f"{name}.csv", columns, tables[name])
        manifest["tables"][name] = {
            "file": f"{name}.csv",
            "rows": len(tables[name]),
            "sha256": digest,
        }

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic clinical study CSVs")
    parser.add_argument("--config", type=Path, default=Path("configs/study_protocol.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--participants", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    manifest = generate_dataset(args.config, args.output_dir, participant_count=args.participants, seed=args.seed)
    print(json.dumps({"output_dir": str(args.output_dir), **manifest}, indent=2))


if __name__ == "__main__":
    main()
