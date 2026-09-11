"""
Reporting layer for SAT-SA.

Takes the already-computed assessment (entity risk scores, findings,
review queue) and writes it out in the formats a supervisor actually
shares: CSV tables for spreadsheet work, and a one-page-per-entity
PDF executive summary. JSON output (the full-fidelity machine-readable
record) is written directly by main.py — this module covers the two
human-facing export formats.
"""

import os

import pandas as pd
from reportlab.lib import colors

from analytics import benchmarking, capabilities
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


def write_csv_exports(out_path: str, risk_scores: pd.DataFrame,
                       exec_gap_findings: pd.DataFrame, neg_space_findings: pd.DataFrame,
                       review_queue: pd.DataFrame) -> list:
    """Writes the four flat CSV exports supervisors ask for; returns the file paths written."""
    os.makedirs(out_path, exist_ok=True)
    written = []

    exports = {
        "entity_risk_scores.csv": risk_scores,
        "execution_gap_findings.csv": exec_gap_findings,
        "negative_space_findings.csv": neg_space_findings,
        "review_queue.csv": review_queue,
    }
    for filename, df in exports.items():
        file_path = os.path.join(out_path, filename)
        if df is None or df.empty:
            pd.DataFrame().to_csv(file_path, index=False)
        else:
            # evidence dicts don't round-trip cleanly to CSV — stringify them
            safe_df = df.copy()
            if "evidence" in safe_df.columns:
                safe_df["evidence"] = safe_df["evidence"].apply(str)
            safe_df.to_csv(file_path, index=False)
        written.append(file_path)

    return written


def _entity_table(entity: dict, styles) -> Table:
    rows = [["Metric", "Value"]]
    rows.append(["Priority rank", str(entity.get("priority_rank"))])
    rows.append(["Supervisory risk score", f"{entity.get('supervisory_risk_score', 0):.2f}"])
    rows.append(["Percentile (peer group)", f"{entity.get('supervisory_risk_score_percentile', 0):.1f}%"])
    rows.append(["Peer group", str(entity.get("peer_group", "all"))])
    rows.append(["Execution gap findings", str(entity.get("execution_gap_count", 0))])
    rows.append(["Negative space findings", str(entity.get("negative_space_count", 0))])

    table = Table(rows, colWidths=[2.5 * inch, 2.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return table


def _ranking_table(entities: list) -> Table:
    rows = [["Rank", "SOC", "Organization", "Sector", "Risk Score", "Percentile", "Findings"]]
    for entity in entities:
        rows.append([
            str(entity.get("priority_rank")),
            entity.get("soc_id", ""),
            entity.get("organization_name", ""),
            str(entity.get("peer_group", "all")),
            f"{entity.get('supervisory_risk_score', 0):.1f}",
            f"{entity.get('supervisory_risk_score_percentile', 0):.0f}%",
            str(entity.get("execution_gap_count", 0) + entity.get("negative_space_count", 0)),
        ])

    table = Table(rows, colWidths=[0.5 * inch, 0.8 * inch, 1.7 * inch, 0.9 * inch, 0.8 * inch, 0.75 * inch, 0.65 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return table


def _risk_drivers_table(entity: dict) -> Table:
    breakdown = [c for c in entity.get("score_breakdown", []) if c.get("contribution", 0) > 0]
    breakdown = sorted(breakdown, key=lambda c: c.get("contribution", 0), reverse=True)

    rows = [["Finding Type", "Raw Count", "Weight", "Contribution"]]
    for component in breakdown[:8]:
        rows.append([
            component.get("finding_type", "").replace("_", " ").title(),
            str(component.get("raw_count", 0)),
            f"{component.get('weight', 0):.2f}",
            f"{component.get('contribution', 0):.2f}",
        ])

    table = Table(rows, colWidths=[2.2 * inch, 0.9 * inch, 0.8 * inch, 1.1 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5f3a1f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return table


def _findings_section(findings: list, heading: str, styles, body_style, limit: int = 5) -> list:
    elements = [Paragraph(heading, styles["Heading4"])]
    if not findings:
        elements.append(Paragraph("None recorded.", body_style))
        return elements

    for finding in findings[:limit]:
        label = finding.get("finding_type", "").replace("_", " ").title()
        severity = finding.get("severity", "")
        elements.append(Paragraph(
            f"<b>{label}</b>"
            + (f" ({severity})" if severity else "")
            + f" — {finding.get('rationale', '')}",
            body_style,
        ))
        evidence = finding.get("evidence", {})
        if evidence:
            evidence_str = ", ".join(f"{k}: {v}" for k, v in evidence.items())
            elements.append(Paragraph(f"<i>Evidence — {evidence_str}</i>", body_style))

    if len(findings) > limit:
        elements.append(Paragraph(
            f"...and {len(findings) - limit} more (see full JSON/CSV export).",
            body_style,
        ))
    return elements


def _capability_table(entity: dict, config: dict) -> Table:
    results = capabilities.assess_entity(entity, config)
    results.sort(key=lambda r: (-r.contribution, r.area.label))

    rows = [["Capability Area", "Findings", "Contribution", "Standing"]]
    for result in results:
        rows.append([
            result.area.label,
            f"{result.finding_count:,}" if result.assessed else "—",
            f"{result.contribution:.2f}" if result.assessed else "—",
            result.status,
        ])

    table = Table(rows, colWidths=[1.9 * inch, 0.8 * inch, 1.0 * inch,
                                     1.3 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return table


def _benchmark_table(position) -> Table:
    rows = [["Finding Type", "Per 100 alerts", "Peer Median",
              "Compared with peers"]]
    for comparison in position.notable(limit=6):
        rows.append([
            comparison.finding_type.replace("_", " ").title(),
            f"{comparison.entity_rate:.2f}",
            f"{comparison.peer_median_rate:.2f}",
            comparison.label(),
        ])

    table = Table(rows, colWidths=[1.7 * inch, 0.9 * inch, 0.8 * inch,
                                     2.6 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return table


def _trend_table(report) -> Table:
    rows = [["Entity", "First", "Latest", "Change", "Direction", "Ranking"]]
    ranks = {r.soc_id: r for r in report.rank_changes}

    for soc_id in sorted(report.risk_score):
        series = report.risk_score[soc_id]
        rank = ranks.get(soc_id)
        rows.append([
            soc_id,
            f"{series.first:,.1f}" if series.first is not None else "—",
            f"{series.last:,.1f}" if series.last is not None else "—",
            series.change_label(),
            series.direction,
            rank.label if rank else "—",
        ])

    table = Table(rows, colWidths=[0.8 * inch, 0.6 * inch, 0.6 * inch,
                                     2.0 * inch, 0.7 * inch, 1.3 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return table


def write_pdf_report(out_path: str, assessment_results: dict,
                       config: dict = None, trend_report=None) -> str:
    """
    The supervisory assessment report.

    Structure: Executive Summary -> Dataset Validation -> Entity Ranking
    -> Peer Benchmarking -> Trends (only when more than one period was
    assessed) -> one section per entity covering execution gaps,
    negative space, risk drivers and capability standing.

    Sections appear only when the data supports them. Peer benchmarking
    is omitted where every peer group is too small to compare against;
    trends are omitted entirely for a single period rather than drawn
    through one point; capability standing is omitted when no mapping is
    configured. A report that prints an empty section implies the tool
    looked and found nothing, which is a different claim from not having
    looked.

    Recommended Actions remains absent: there is no
    recommendation-generation logic in the pipeline, and the optional
    narration layer restates findings rather than deciding what to do
    about them. A placeholder would misrepresent the tool.
    """
    config = config or {}
    os.makedirs(out_path, exist_ok=True)
    file_path = os.path.join(out_path, "executive_summary.pdf")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("SATSATitle", parent=styles["Title"], fontSize=20)
    h2_style = ParagraphStyle("SATSAH2", parent=styles["Heading2"], spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle("SATSABody", parent=styles["BodyText"], fontSize=9, leading=12)
    caption_style = ParagraphStyle("SATSACaption", parent=styles["BodyText"], fontSize=8,
                                    textColor=colors.grey)

    doc = SimpleDocTemplate(file_path, pagesize=letter,
                             topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    story = []

    metadata = assessment_results.get("run_metadata", {})
    entities = assessment_results.get("entities", [])

    # ---- Executive Summary ----
    story.append(Paragraph("SAT-SA Supervisory Assessment — Executive Summary", title_style))
    story.append(Paragraph(
        f"Entities assessed: {metadata.get('entities_assessed', len(entities))} &nbsp;|&nbsp; "
        f"Alerts reviewed: {metadata.get('total_alerts', 0):,} &nbsp;|&nbsp; "
        f"Total findings: {metadata.get('total_findings', 0):,}",
        caption_style,
    ))
    story.append(Paragraph(
        "Findings below are evidence-based indicators produced by deterministic rules. "
        "They require supervisory review and are not, on their own, determinations of "
        "wrongdoing or performance failure. Risk percentiles are computed within each "
        "entity's peer group (sector), not against the full dataset.",
        caption_style,
    ))
    if entities:
        highest = entities[0]
        story.append(Paragraph(
            f"Highest-priority entity: <b>{highest.get('organization_name', highest.get('soc_id'))}</b> "
            f"({highest.get('soc_id')}), risk score {highest.get('supervisory_risk_score', 0):.1f}, "
            f"{highest.get('execution_gap_count', 0) + highest.get('negative_space_count', 0)} total findings.",
            body_style,
        ))
    story.append(Spacer(1, 0.15 * inch))

    # ---- Dataset validation ----
    validation = assessment_results.get("validation_report", {}) or {}
    story.append(Paragraph("Dataset Validation", h2_style))
    if validation.get("issue_count"):
        story.append(Paragraph(
            f"{validation.get('error_count', 0)} blocking error(s) and "
            f"{validation.get('warning_count', 0)} warning(s) were recorded "
            f"when the submission was read. An assessment is only produced "
            f"when no blocking errors remain.",
            body_style))
        for issue in validation.get("issues", [])[:10]:
            rows = (f" ({issue.get('row_count')} rows)"
                    if issue.get("row_count") else "")
            story.append(Paragraph(
                f"<b>[{issue.get('severity')}]</b> {issue.get('table')}: "
                f"{issue.get('message')}{rows}", body_style))
    else:
        story.append(Paragraph(
            "The submission passed structural validation with no issues "
            "recorded.", body_style))
    story.append(Spacer(1, 0.12 * inch))

    # ---- Entity Ranking ----
    story.append(Paragraph("Entity Ranking", h2_style))
    if entities:
        story.append(_ranking_table(entities))
    else:
        story.append(Paragraph("No entities assessed.", body_style))

    # ---- Evidence completeness ----
    # Printed immediately after the ranking and before anything else is
    # read into it. The risk score counts what the records allow it to
    # count, so an entity whose records are largely absent will rank
    # low for a reason that has nothing to do with how well it was run.
    # A report that showed the ranking without this could be read
    # exactly backwards.
    limited = [
        entity for entity in entities
        if (entity.get("evidence_completeness") or {}).get("limited")
    ]
    if limited:
        story.append(Spacer(1, 0.14 * inch))
        story.append(Paragraph("Evidence Completeness", h2_style))
        story.append(Paragraph(
            "These entities did not submit enough of the records the "
            "checks depend on for those checks to run fully. Their "
            "finding counts and risk scores are a floor, not a "
            "measurement of how much went wrong. This is not itself a "
            "finding: absent records may reflect an incomplete export "
            "or a system that stores them elsewhere.",
            caption_style))
        for entity in limited:
            completeness = entity["evidence_completeness"]
            story.append(Paragraph(
                f"<b>{entity.get('soc_id')}</b> — "
                f"{completeness['overall_coverage'] * 100:.0f}% of the "
                f"records these checks read were present. Limited: "
                f"{', '.join(completeness['suppressed_rules'])}.",
                body_style))

    # ---- Peer benchmarking ----
    # Omitted entirely where no peer group is large enough to compare
    # against. An empty section would imply the tool looked and found
    # nothing, which is a different claim from not having looked.
    positions = benchmarking.build_positions(assessment_results)
    comparable = [p for p in positions if p.comparable]
    if comparable:
        story.append(Spacer(1, 0.14 * inch))
        story.append(Paragraph("Peer Benchmarking", h2_style))
        story.append(Paragraph(
            benchmarking.benchmarking_caveat(
                benchmarking.build_peer_groups(assessment_results)),
            caption_style))
        worst = comparable[0]
        story.append(Paragraph(
            f"<b>{worst.soc_id}</b> — {worst.position_label}.", body_style))
        story.append(_benchmark_table(worst))
    elif positions:
        story.append(Spacer(1, 0.14 * inch))
        story.append(Paragraph("Peer Benchmarking", h2_style))
        story.append(Paragraph(
            benchmarking.benchmarking_caveat(
                benchmarking.build_peer_groups(assessment_results)),
            caption_style))

    # ---- Trends ----
    # Only when more than one period was actually assessed. A single
    # period yields no trend, and drawing one would be fabrication.
    if trend_report is not None and getattr(trend_report, "available", False):
        story.append(Spacer(1, 0.14 * inch))
        story.append(Paragraph("Trends Across Submission Periods", h2_style))
        story.append(Paragraph(trend_report.summary(), body_style))
        story.append(_trend_table(trend_report))

    story.append(PageBreak())

    # ---- Per-entity: Major Execution Gaps / Negative Space / Evidence / Risk Drivers ----
    for entity in entities:
        story.append(Paragraph(
            f"{entity.get('priority_rank')}. {entity.get('organization_name', entity.get('soc_id'))} "
            f"({entity.get('soc_id')})",
            h2_style,
        ))
        story.append(_entity_table(entity, styles))
        story.append(Spacer(1, 0.12 * inch))

        exec_findings = entity.get("execution_gap_findings", [])
        neg_findings = entity.get("negative_space_findings", [])

        story.append(Spacer(1, 0.08 * inch))
        story.extend(_findings_section(
            exec_findings, "Major Execution Gaps", styles, body_style,
        ))

        story.append(Spacer(1, 0.1 * inch))
        story.extend(_findings_section(
            neg_findings, "Negative Space", styles, body_style,
        ))

        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("Risk Drivers", styles["Heading4"]))
        if entity.get("score_breakdown"):
            story.append(_risk_drivers_table(entity))
        else:
            story.append(Paragraph("No score breakdown available.", body_style))

        # Capability standing, omitted when no mapping is configured.
        if capabilities.load_areas(config):
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph(
                "Supervisory Capability Areas", styles["Heading4"]))
            story.append(Paragraph(
                capabilities.coverage_statement(config), caption_style))
            story.append(_capability_table(entity, config))

        story.append(PageBreak())

    if story and isinstance(story[-1], PageBreak):
        story.pop()

    doc.build(story)
    return file_path
