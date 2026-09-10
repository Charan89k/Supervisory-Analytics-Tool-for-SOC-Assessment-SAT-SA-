import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="SAT-SA | SOC Supervisory Analytics",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

RESULTS_FILE = Path(__file__).parent / "outputs" / "assessment_results.json"


# ─────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────

@st.cache_data
def load_results():
    if not RESULTS_FILE.exists():
        return None

    with RESULTS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


data = load_results()

if data is None:
    st.error(
        "Assessment results not found. Run the SAT-SA analytics pipeline first."
    )
    st.stop()


metadata = data.get("run_metadata", {})
entities = data.get("entities", [])


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def all_findings():
    findings = []

    for entity in entities:
        for finding in entity.get("execution_gap_findings", []):
            item = dict(finding)
            item["category"] = "Execution Gap"
            findings.append(item)

        for finding in entity.get("negative_space_findings", []):
            item = dict(finding)
            item["category"] = "Negative Space"
            findings.append(item)

    return findings


findings = all_findings()

finding_df = pd.DataFrame(findings)

if not finding_df.empty:
    finding_df["finding_type_display"] = (
        finding_df["finding_type"]
        .str.replace("_", " ")
        .str.title()
    )


# ─────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────

st.title("🛡️ SAT-SA")
st.subheader("Supervisory Analytics Tool for SOC Assessment")

st.caption(
    "NCIIPC / CSE supervisory analytics • "
    "Execution Gaps • Negative Space • Evidence-based findings"
)


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────

st.sidebar.header("Assessment")

st.sidebar.success("Assessment data loaded")

selected_soc = st.sidebar.selectbox(
    "SOC / CSE",
    ["All SOCs"] + [
        f"{e['soc_id']} — {e['organization_name']}"
        for e in entities
    ],
)

if selected_soc == "All SOCs":
    selected_entity = None
else:
    selected_soc_id = selected_soc.split(" — ")[0]
    selected_entity = next(
        (e for e in entities if e["soc_id"] == selected_soc_id),
        None,
    )


# ─────────────────────────────────────────────
# Executive KPIs
# ─────────────────────────────────────────────

st.markdown("## Executive Overview")

total_socs = metadata.get("entities_assessed", len(entities))
total_alerts = metadata.get("total_alerts", 0)
total_findings = metadata.get("total_findings", len(findings))

highest_risk = max(
    entities,
    key=lambda x: x.get("supervisory_risk_score", 0),
    default=None,
)

highest_risk_name = (
    highest_risk["organization_name"]
    if highest_risk
    else "N/A"
)

highest_risk_score = (
    highest_risk.get("supervisory_risk_score", 0)
    if highest_risk
    else 0
)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("SOC Entities", total_socs)

with col2:
    st.metric("Alerts Assessed", f"{total_alerts:,}")

with col3:
    st.metric("Total Findings", f"{total_findings:,}")

with col4:
    st.metric(
        "Highest Risk",
        f"{highest_risk_score:.1f}",
        help=highest_risk_name,
    )


# ─────────────────────────────────────────────
# SOC Risk Ranking
# ─────────────────────────────────────────────

st.markdown("## 🏆 SOC Risk Ranking")

ranking_rows = []

for entity in entities:
    ranking_rows.append(
        {
            "Rank": entity.get("priority_rank"),
            "SOC": entity.get("soc_id"),
            "Organization": entity.get("organization_name"),
            "Risk Score": entity.get("supervisory_risk_score", 0),
            "Execution Gaps": entity.get("execution_gap_count", 0),
            "Negative Space": entity.get("negative_space_count", 0),
        }
    )

ranking_df = pd.DataFrame(ranking_rows)

if not ranking_df.empty:
    st.dataframe(
        ranking_df,
        width='stretch',
        hide_index=True,
        column_config={
            "Risk Score": st.column_config.ProgressColumn(
                "Risk Score",
                min_value=0,
                max_value=max(
                    100,
                    float(ranking_df["Risk Score"].max()),
                ),
                format="%.1f",
            ),
        },
    )

    fig = px.bar(
        ranking_df.sort_values("Risk Score"),
        x="Risk Score",
        y="Organization",
        orientation="h",
        text="Risk Score",
        title="Supervisory Risk Score by SOC",
    )

    fig.update_traces(texttemplate="%{text:.1f}")
    fig.update_layout(height=400)

    st.plotly_chart(fig, width='stretch')


# ─────────────────────────────────────────────
# Peer Comparison
# ─────────────────────────────────────────────

st.markdown("## 🧭 Peer Comparison")

peer_rows = []

for entity in entities:
    peer_rows.append(
        {
            "SOC": entity.get("soc_id"),
            "Organization": entity.get("organization_name"),
            "Peer Group": entity.get("peer_group", "all"),
            "Risk Score": entity.get("supervisory_risk_score", 0),
            "Percentile": entity.get("supervisory_risk_score_percentile", 0),
        }
    )

peer_df = pd.DataFrame(peer_rows)

if not peer_df.empty:
    peer_groups = sorted(peer_df["Peer Group"].dropna().unique())

    if len(peer_groups) > 1:
        group_choice = st.selectbox(
            "Peer group",
            ["All Groups"] + list(peer_groups),
            key="peer_group_select",
        )

        if group_choice != "All Groups":
            peer_df = peer_df[peer_df["Peer Group"] == group_choice]
    else:
        st.caption(
            f"All entities currently share a single peer group "
            f"({peer_groups[0]!r}) — percentile is computed across all "
            f"assessed SOCs."
        )

    fig_peer = px.bar(
        peer_df.sort_values("Percentile"),
        x="Percentile",
        y="Organization",
        orientation="h",
        text="Percentile",
        color="Peer Group" if len(peer_groups) > 1 else None,
        title="Risk Score Percentile vs. Peers",
    )

    fig_peer.update_traces(texttemplate="%{text:.0f}%")
    fig_peer.update_layout(height=400, xaxis_range=[0, 100])

    st.plotly_chart(fig_peer, width='stretch')

    st.dataframe(
        peer_df.sort_values("Percentile", ascending=False),
        width='stretch',
        hide_index=True,
        column_config={
            "Percentile": st.column_config.ProgressColumn(
                "Percentile",
                min_value=0,
                max_value=100,
                format="%.0f%%",
            ),
        },
    )


# ─────────────────────────────────────────────
# Selected SOC details
# ─────────────────────────────────────────────

if selected_entity:

    st.markdown("---")
    st.markdown(
        f"## 🔎 {selected_entity['organization_name']}"
    )

    detail_col1, detail_col2, detail_col3, detail_col4 = st.columns(4)

    with detail_col1:
        st.metric(
            "Risk Score",
            f"{selected_entity.get('supervisory_risk_score', 0):.1f}",
        )

    with detail_col2:
        st.metric(
            "Risk Percentile",
            f"{selected_entity.get('supervisory_risk_score_percentile', 0):.1f}%",
        )

    with detail_col3:
        st.metric(
            "Execution Gaps",
            selected_entity.get("execution_gap_count", 0),
        )

    with detail_col4:
        st.metric(
            "Negative Space",
            selected_entity.get("negative_space_count", 0),
        )

    # Score breakdown
    st.markdown("### Score Breakdown")

    breakdown = selected_entity.get("score_breakdown", [])

    if breakdown:
        breakdown_df = pd.DataFrame(breakdown)

        breakdown_df["finding_type"] = (
            breakdown_df["finding_type"]
            .str.replace("_", " ")
            .str.title()
        )

        st.dataframe(
            breakdown_df[
                [
                    "finding_type",
                    "raw_count",
                    "normalized_per_100",
                    "weight",
                    "contribution",
                ]
            ],
            width='stretch',
            hide_index=True,
        )


# ─────────────────────────────────────────────
# Finding Analytics
# ─────────────────────────────────────────────

st.markdown("---")
st.markdown("## 🚨 Finding Analytics")

if finding_df.empty:

    st.info("No findings were generated by the assessment.")

else:

    if selected_entity:
        filtered_findings = finding_df[
            finding_df["soc_id"] == selected_entity["soc_id"]
        ].copy()
    else:
        filtered_findings = finding_df.copy()

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Execution Gaps")

        execution_df = filtered_findings[
            filtered_findings["category"] == "Execution Gap"
        ]

        execution_counts = (
            execution_df["finding_type_display"]
            .value_counts()
            .reset_index()
        )

        execution_counts.columns = ["Finding", "Count"]

        if not execution_counts.empty:
            fig_exec = px.bar(
                execution_counts,
                x="Count",
                y="Finding",
                orientation="h",
                text="Count",
                title="Execution Gap Findings",
            )

            fig_exec.update_traces(textposition="outside")
            fig_exec.update_layout(height=450)

            st.plotly_chart(
                fig_exec,
                width='stretch',
            )

    with col2:
        st.markdown("### Negative Space")

        negative_df = filtered_findings[
            filtered_findings["category"] == "Negative Space"
        ]

        negative_counts = (
            negative_df["finding_type_display"]
            .value_counts()
            .reset_index()
        )

        negative_counts.columns = ["Finding", "Count"]

        if not negative_counts.empty:
            fig_negative = px.bar(
                negative_counts,
                x="Count",
                y="Finding",
                orientation="h",
                text="Count",
                title="Negative Space Findings",
            )

            fig_negative.update_traces(textposition="outside")
            fig_negative.update_layout(height=450)

            st.plotly_chart(
                fig_negative,
                width='stretch',
            )

        else:
            st.success("No negative-space findings for this selection.")


# ─────────────────────────────────────────────
# Supervisory Review Queue
# ─────────────────────────────────────────────

st.markdown("---")
st.markdown("## 📋 Supervisory Review Queue")

st.caption(
    "The highest-priority cases for a human reviewer to inspect first. "
    "Multiple findings on the same alert are correlated into one case, "
    "ranked by case priority (sum of finding weight × severity boost "
    "across every finding on that alert)."
)

review_queue = data.get("review_queue", [])

if not review_queue:
    st.info("No items in the supervisory review queue.")

else:
    review_df = pd.DataFrame(review_queue)

    if selected_entity:
        review_df = review_df[
            review_df["soc_id"] == selected_entity["soc_id"]
        ].copy()

    if review_df.empty:
        st.info("No review-queue items for this selection.")

    else:
        review_df = review_df.sort_values(["case_rank", "queue_rank"])

        review_df["finding_type_display"] = (
            review_df["finding_type"]
            .str.replace("_", " ")
            .str.title()
        )

        # One row per case (not per finding) for the top-level table —
        # correlated findings on the same alert are one case.
        case_summary = (
            review_df.groupby("case_rank")
            .agg(
                soc_id=("soc_id", "first"),
                alert_id=("alert_id", "first"),
                case_id=("case_id", "first"),
                assigned_analyst_id=("assigned_analyst_id", "first"),
                case_priority=("case_priority", "first"),
                case_finding_count=("case_finding_count", "first"),
                finding_types=("finding_type_display", lambda s: ", ".join(s)),
                worst_severity=("severity", "first"),
            )
            .reset_index()
            .sort_values("case_rank")
        )

        st.dataframe(
            case_summary,
            width='stretch',
            hide_index=True,
            column_config={
                "case_rank": st.column_config.NumberColumn("Rank"),
                "case_priority": st.column_config.NumberColumn(
                    "Priority", format="%.1f"
                ),
                "case_finding_count": st.column_config.NumberColumn(
                    "# Findings"
                ),
                "finding_types": st.column_config.Column(
                    "Findings on this case", width="large"
                ),
                "worst_severity": st.column_config.Column("Severity"),
            },
        )

        case_index = st.number_input(
            "Select case row",
            min_value=0,
            max_value=len(case_summary) - 1,
            value=0,
            step=1,
            key="review_queue_row",
        )

        selected_case_rank = case_summary.iloc[int(case_index)]["case_rank"]
        case_findings = review_df[
            review_df["case_rank"] == selected_case_rank
        ].sort_values("queue_priority", ascending=False)

        case_count = int(case_findings.iloc[0]["case_finding_count"])

        if case_count > 1:
            st.markdown(
                f"#### {case_count} correlated findings on this alert "
                f"— all part of the same case"
            )
        else:
            st.markdown("#### Finding detail")

        for _, finding_row in case_findings.iterrows():
            with st.expander(
                f"{finding_row.get('finding_type_display', 'Unknown')} "
                f"({finding_row.get('severity', 'Unknown')})",
                expanded=(case_count == 1),
            ):
                st.markdown("**Why this was flagged**")
                st.write(
                    finding_row.get("rationale", "No rationale available.")
                )

                col_evidence, col_meta = st.columns(2)

                with col_evidence:
                    st.markdown("**Evidence**")
                    evidence = finding_row.get("evidence", {})
                    if evidence:
                        st.json(evidence)
                    else:
                        st.info("No structured evidence attached.")

                with col_meta:
                    st.markdown("**Priority breakdown**")
                    st.write(
                        {
                            "finding_weight": finding_row.get("finding_weight"),
                            "severity_boost": finding_row.get("severity_boost"),
                            "queue_priority": finding_row.get("queue_priority"),
                        }
                    )


# ─────────────────────────────────────────────
# Finding Explorer
# ─────────────────────────────────────────────

st.markdown("---")
st.markdown("## 🔬 Finding Explorer")

if finding_df.empty:

    st.info("There are no findings to investigate.")

else:

    explorer_df = filtered_findings.copy()

    available_types = sorted(
        explorer_df["finding_type_display"].dropna().unique()
    )

    selected_type = st.selectbox(
        "Finding type",
        ["All Types"] + available_types,
    )

    if selected_type != "All Types":
        explorer_df = explorer_df[
            explorer_df["finding_type_display"] == selected_type
        ]

    if explorer_df.empty:
        st.info("No findings match the selected filter.")

    else:

        display_columns = [
            column
            for column in [
                "finding_type_display",
                "severity",
                "soc_id",
                "alert_id",
                "case_id",
                "assigned_analyst_id",
                "rule_id",
            ]
            if column in explorer_df.columns
        ]

        st.dataframe(
            explorer_df[display_columns].head(200),
            width='stretch',
            hide_index=True,
        )

        finding_index = st.number_input(
            "Select finding row",
            min_value=0,
            max_value=len(explorer_df) - 1,
            value=0,
            step=1,
        )

        selected_finding = explorer_df.iloc[int(finding_index)]

        st.markdown("### Finding Details")

        detail_left, detail_right = st.columns(2)

        with detail_left:

            st.markdown("#### What happened?")

            st.write(
                selected_finding.get(
                    "finding_type_display",
                    "Unknown",
                )
            )

            st.markdown("#### Severity")

            st.write(
                selected_finding.get(
                    "severity",
                    "Unknown",
                )
            )

            st.markdown("#### Rule")

            st.code(
                str(
                    selected_finding.get(
                        "rule_id",
                        "N/A",
                    )
                )
            )

        with detail_right:

            st.markdown("#### Why was it flagged?")

            st.write(
                selected_finding.get(
                    "rationale",
                    "No rationale available.",
                )
            )

            st.markdown("#### Evidence")

            evidence = selected_finding.get("evidence", {})

            if evidence:
                st.json(evidence)

            else:
                st.info("No structured evidence attached.")


# ─────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────

st.markdown("---")

st.caption(
    "SAT-SA • Deterministic supervisory analytics • "
    "Findings are evidence-based indicators and require supervisory review."
)
