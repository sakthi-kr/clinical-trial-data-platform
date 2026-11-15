# Architecture and design decisions

1. **Synthetic only:** no data from real patients; use a four-site fictional clinical study with a clear protocol.
2. **Local first:** DuckDB is a zero-server analytical database that supports typed tables, constraints and transactions on Windows.
3. **Manifest identity:** a SHA-256 hash of the exact manifest bytes identifies a generated batch; the manifest contains each source CSV checksum. This is a reproducibility mechanism, not an authenticity certificate.
4. **Fail whole batch:** Phase 2 rejects structurally bad datasets and logs failures. We do not silently drop malformed rows or automatically repair them. Clinical-style issue handling starts in Phase 3.
5. **Database transactions:** each load is an ACID transaction; row-count mismatch or cast failures roll back the complete dataset.
6. **Composite foreign keys:** keys include `dataset_id` to allow two synthetic generations with identical IDs to coexist safely.
7. **Explicit numeric precision:** measurement values use `DECIMAL(18,6)`, with more than six decimal places rejected by preflight; date fields use SQL DATE.
8. **Separate quality rules:** Phase 2 treats unusual values as structurally valid; the future Phase 3 validation engine determines whether a recorded value warrants review.
9. **No CDISC-compliance claim:** source tables resemble common clinical study concepts but are not SDTM domains.
10. **No new cloud/API:** no medical APIs, Snowflake, proprietary connectors or paid infrastructure are required to reproduce Phase 2.
