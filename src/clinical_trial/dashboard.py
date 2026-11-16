"""Local, read-only dashboard for simulated clinical-study data quality.

Run with: python -m streamlit run src/clinical_trial/dashboard.py
"""
from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from clinical_trial.review import ReviewError, STATUS_CHOICES, record_review

from clinical_trial.dashboard_data import (
    DashboardDataError,
    count_by,
    filter_issues,
    issue_csv,
    list_datasets,
    load_snapshot,
)


st.set_page_config(
    page_title="Study Data Quality | Clinical Trial Platform",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _bar_chart(rows: list[dict], title: str, *, horizontal: bool = False) -> None:
    """Draw a compact chart using counts already filtered in the app."""
    if not rows:
        st.caption("No findings to chart for the current selection.")
        return
    labels = [str(x["name"]) for x in rows]
    counts = [x["count"] for x in rows]
    chart = go.Figure()
    if horizontal:
        chart.add_trace(go.Bar(
            x=list(reversed(counts)), y=list(reversed(labels)), orientation="h",
            marker_color="#346C84", text=list(reversed(counts)), textposition="outside",
            hovertemplate="%{y}: %{x} findings<extra></extra>",
        ))
        chart.update_layout(xaxis_title="Findings", yaxis_title=None)
    else:
        chart.add_trace(go.Bar(
            x=labels, y=counts, marker_color="#346C84", text=counts,
            textposition="outside", hovertemplate="%{x}: %{y} findings<extra></extra>",
        ))
        chart.update_layout(xaxis_title=None, yaxis_title="Findings")
    chart.update_layout(
        title=dict(text=title, font=dict(size=15)),
        margin=dict(l=12, r=20, t=46, b=20),
        height=320,
        showlegend=False,
    )
    st.plotly_chart(chart, width="stretch", config={"displayModeBar": False})


def main() -> None:
    st.title("Clinical Study Data Quality")
    st.caption("Synthetic research data · Local DuckDB · Study data read-only · Local review notes")

    st.sidebar.header("Data source")
    default = (
        "data/challenge.duckdb" if Path("data/challenge.duckdb").is_file()
        else "data/study.duckdb"
    )
    db_name = st.sidebar.text_input("DuckDB file", value=default)
    db_path = Path(db_name).expanduser()
    if not db_path.is_file():
        st.info("No DuckDB file found at this path. Generate and ingest a synthetic dataset first.")
        st.code("python -m clinical_trial.ingest --input-dir data/generated --db data/study.duckdb", language="bash")
        st.stop()

    try:
        datasets = list_datasets(db_path)
    except DashboardDataError as exc:
        st.error(str(exc))
        st.stop()
    if not datasets:
        st.warning("This database does not contain an ingested study dataset.")
        st.stop()

    dataset_lookup = {d["dataset_id"]: d for d in datasets}
    dataset_id = st.sidebar.selectbox(
        "Dataset", list(dataset_lookup),
        format_func=lambda key: f"{dataset_lookup[key]['study_id']} · {key[:12]}",
    )
    try:
        snapshot = load_snapshot(db_path, dataset_id)
    except DashboardDataError as exc:
        st.error(str(exc))
        st.stop()

    counts = snapshot["counts"]
    all_issues = snapshot["issues"]
    history = snapshot["history"]
    review_history = snapshot.get("review_history", [])
    severities = {level: sum(i["severity"] == level for i in all_issues)
                  for level in ("ERROR", "WARNING")}

    st.sidebar.divider()
    st.sidebar.subheader("Findings filters")
    sites = {site["site_id"]: site["site_name"] for site in snapshot["sites"]}
    site_option = st.sidebar.selectbox(
        "Site", ["All sites", *sites],
        format_func=lambda v: v if v == "All sites" else f"{v} · {sites[v]}",
    )
    visits = sorted(set(snapshot["visit_codes"]) | {x["visit_code"] for x in all_issues})
    visit_option = st.sidebar.selectbox("Visit", ["All visits", *visits])
    level_option = st.sidebar.selectbox("Severity", ["All severities", "ERROR", "WARNING"])
    review_option = st.sidebar.selectbox("Review status",
        ["All review statuses", "UNREVIEWED", *STATUS_CHOICES])
    rule_options = sorted({x["rule_id"] for x in all_issues})
    rules_selected = st.sidebar.multiselect("Rule IDs", rule_options)
    participant_search = st.sidebar.text_input("Participant ID contains", placeholder="e.g. P000015")

    filtered = filter_issues(
        all_issues,
        site_id=None if site_option == "All sites" else site_option,
        visit_code=None if visit_option == "All visits" else visit_option,
        severity=None if level_option == "All severities" else level_option,
        rule_ids=rules_selected or None,
        participant_text=participant_search,
        review_status=None if review_option == "All review statuses" else review_option,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Participants", f"{counts['participants']:,}")
    c2.metric("Visits", f"{counts['visits']:,}")
    c3.metric("Current findings", f"{len(all_issues):,}")
    c4.metric("Errors", f"{severities['ERROR']:,}")
    c5.metric("Warnings", f"{severities['WARNING']:,}")

    if not history:
        st.warning("No quality evaluation is recorded for this dataset. Run the quality command before viewing findings.")
        st.code(f"python -m clinical_trial.quality --db {db_name}", language="bash")
    else:
        latest = history[0]
        st.caption(f"Last evaluation: {latest['evaluated_at']} · Ruleset SHA-256: {latest['ruleset_sha256'][:12]}…")

    overview, findings, audit, review_activity = st.tabs(
        ["Overview", "Findings", "Evaluation history", "Review activity"]
    )
    with overview:
        st.subheader("Findings at a glance")
        st.caption(f"{len(filtered):,} findings match the current filters (out of {len(all_issues):,} total).")
        lcol, rcol = st.columns(2)
        with lcol:
            _bar_chart(count_by(filtered, "rule_id")[:12], "Findings by rule", horizontal=True)
            _bar_chart(count_by(filtered, "visit_code"), "Findings by visit")
        with rcol:
            _bar_chart(count_by(filtered, "site_id"), "Findings by site")
            _bar_chart(count_by(filtered, "severity"), "Findings by severity")
        if not all_issues and history:
            st.info("No findings in the latest quality review. This only means the synthetic records matched the configured checks.")
        elif not filtered and all_issues:
            st.info("No findings match these filters. Try broadening the selection in the sidebar.")

    with findings:
        st.subheader("Record-level findings")
        st.caption("All rows are simulated findings. Review notes do not change study records.")
        st.download_button(
            "Download filtered findings (CSV)",
            issue_csv(filtered),
            file_name=f"quality_findings_{dataset_id[:12]}.csv",
            mime="text/csv",
            disabled=not filtered,
        )
        columns = (
            "rule_id", "severity", "site_id", "visit_code", "participant_id",
            "source_table", "record_id", "review_status", "message", "observed_value", "expected_value",
        )
        st.dataframe(
            [{k: row.get(k) for k in columns} for row in filtered],
            width="stretch", hide_index=True,
        )
        st.caption(f"Showing {len(filtered):,} of {len(all_issues):,} current findings.")
        if filtered:
            labels = [f"{row['rule_id']} · {row['record_id']}" for row in filtered]
            selected_idx = st.selectbox("Inspect a finding", range(len(filtered)),
                                        format_func=lambda idx: labels[idx])
            selected = filtered[selected_idx]
            with st.container(border=True):
                st.markdown(f"**{selected['rule_id']} — {selected['message']}**")
                k1, k2 = st.columns(2)
                with k1:
                    st.write("**Observed:**", selected.get("observed_value") or "Not recorded")
                    st.write("**Participant:**", selected.get("participant_id") or "N/A")
                    st.write("**Site:**", selected.get("site_id") or "N/A")
                with k2:
                    st.write("**Expected:**", selected.get("expected_value") or "Not specified")
                    st.write("**Record ID:**", selected["record_id"])
                    st.write("**Visit:**", selected.get("visit_code") or "N/A")
            st.caption("Review decisions are local annotations, not changes to observations.")
            with st.form(key=f"review_{dataset_id[:12]}_{selected['issue_id'][:12]}"):
                st.markdown("**Record a review decision**")
                current_status = selected.get("review_status", "UNREVIEWED")
                initial_status = STATUS_CHOICES.index(current_status) if current_status in STATUS_CHOICES else 0
                new_status = st.selectbox("Review status", STATUS_CHOICES, index=initial_status)
                reviewer_label = st.text_input(
                    "Reviewer label (not authenticated)",
                    value=selected.get("reviewer") or "",
                    max_chars=80,
                )
                review_note = st.text_area(
                    "Review note (synthetic data only)",
                    value=selected.get("review_note") or "",
                    max_chars=1000,
                )
                submitted = st.form_submit_button("Save review")
                if submitted:
                    try:
                        record_review(
                            db_path, dataset_id=dataset_id, issue_id=selected["issue_id"],
                            status=new_status, reviewer=reviewer_label, note=review_note,
                            expected_version=int(selected.get("review_version", 0)),
                        )
                    except ReviewError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Review event saved.")
                        st.rerun()


    with audit:
        st.subheader("Quality evaluations")
        if not history:
            st.info("No recorded evaluations yet.")
        else:
            st.caption("The latest evaluation determines the current issue list. Prior evaluation summaries remain available.")
            st.dataframe(
                [{"Evaluation time": str(run["evaluated_at"]),
                  "Run ID": run["run_id"],
                  "Issue count": run["issue_count"],
                  "Ruleset SHA-256": run["ruleset_sha256"]}
                 for run in history],
                width="stretch", hide_index=True,
            )
        st.divider()
        st.write("**Study source**")
        st.code(dataset_id)
        st.caption("Audit information is read-only. Data imports and evaluations are performed through separate commands.")

    with review_activity:
        st.subheader("Review event history")
        st.caption("Appended reviewer decisions; reviewer labels are not verified identities.")
        if not review_history:
            st.info("No review decisions have been recorded for this dataset.")
        else:
            st.dataframe(
                [{
                    "Recorded at": str(row["recorded_at"]),
                    "Issue ID": row["issue_id"],
                    "Status": row["status"],
                    "Reviewer": row["reviewer"],
                    "Note": row["note"],
                    "Version": row["review_version"],
                } for row in reversed(review_history)],
                width="stretch", hide_index=True,
            )
        st.caption("History can include findings that were later changed or disappeared.")

    st.divider()
    st.caption("Educational demonstration with entirely synthetic records. Review thresholds are fictional checks, not clinical reference ranges. No authenticated reviewer identities or regulatory audit trail. Not for clinical use.")


if __name__ == "__main__":
    main()
