"""Verify generated source-file integrity against its manifest.

This command checks file presence, CSV headers, row counts and hashes. It does
NOT perform clinical data-quality validation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from clinical_trial.schema import TABLE_COLUMNS


def verify_manifest(folder: Path) -> list[str]:
    folder = Path(folder)
    manifest_file = folder / "manifest.json"
    if not manifest_file.is_file():
        return [f"Manifest missing: {manifest_file}"]
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [f"Cannot read manifest: {exc}"]
    issues: list[str] = []
    if manifest.get("synthetic_only") is not True:
        issues.append("Manifest does not declare synthetic_only=true")
    recorded_tables = manifest.get("tables", {})
    if set(recorded_tables) != set(TABLE_COLUMNS):
        issues.append("Manifest table set does not match required schema")
    for name, expected_columns in TABLE_COLUMNS.items():
        descriptor = recorded_tables.get(name)
        if not isinstance(descriptor, dict):
            issues.append(f"Missing manifest entry: {name}")
            continue
        path = folder / f"{name}.csv"
        if not path.is_file():
            issues.append(f"Missing CSV file: {name}.csv")
            continue
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if descriptor.get("sha256") != actual_digest:
            issues.append(f"SHA-256 mismatch: {name}.csv")
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if tuple(reader.fieldnames or []) != expected_columns:
                    issues.append(f"Column mismatch: {name}.csv")
                rows = sum(1 for _ in reader)
                if rows != descriptor.get("rows"):
                    issues.append(f"Row-count mismatch: {name}.csv")
        except (UnicodeError, csv.Error, OSError) as exc:
            issues.append(f"Invalid CSV: {name}.csv ({exc})")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify generated CSVs against the file manifest")
    parser.add_argument("--input-dir", type=Path, default=Path("data/generated"))
    args = parser.parse_args()
    issues = verify_manifest(args.input_dir)
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        raise SystemExit(1)
    print(f"PASS: manifest integrity verified for {args.input_dir}")


if __name__ == "__main__":
    main()
