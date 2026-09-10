#!/usr/bin/env python3
"""
SAT-SA Analytics Engine v0.1 — end-to-end pipeline.

Usage:
    python main.py --data ./data/synthetic

Produces:
    outputs/assessment_results.json
"""

import argparse
import json
import os

import yaml
import pandas as pd
import numpy as np

from analytics.loader import load_soc_dataset
from analytics.validator import validate_dataset
from analytics.normalizer import normalize_dataset, build_alerts_enriched
from analytics.metrics.alert_metrics import (
    alert_volume_by_soc, severity_mix_by_soc, true_positive_rate_by_soc, category_coverage_by_soc,
)
from analytics.metrics.analyst_metrics import analyst_workload, flag_overloaded_analysts
from analytics.metrics.case_metrics import (
    case_resolution_time_by_soc, reopen_rate_by_soc, resolution_outcome_mix_by_soc,
)
from analytics.metrics.escalation_metrics import escalation_compliance_by_soc
from analytics.detection.execution_gaps import run_all_execution_gap_detectors
from analytics.detection.negative_space import run_all_negative_space_detectors
from analytics.scoring.benchmark import add_peer_group, percentile_rank_by_peer_group
from analytics.scoring.score import build_finding_counts, compute_entity_risk_scores, score_breakdown_for_entity


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def df_records(df: pd.DataFrame) -> list:
    """DataFrame -> list of JSON-safe dicts (NaN/NaT -> None)."""
    if df is None or df.empty:
        return []
    clean = df.astype(object).where(pd.notnull(df), None)
    return clean.to_dict(orient="records")


def run_pipeline(data_path: str, out_path: str, config_path: str):
    cfg = load_config(config_path)
    steps_completed = []

    def done(label):
        print(f"✓ {label}")
        steps_completed.append(label)

    # ---- Load ----
    data = load_soc_dataset(data_path)
    done("Dataset loaded")

    # ---- Validate ----
    validation_report = validate_dataset(data)
    if validation_report.has_errors:
        print(validation_report.summary())
        print("\n✗ Dataset validation FAILED — fix the errors above before proceeding.")
        return
    done("Dataset validated")

    # ---- Normalize ----
    data = normalize_dataset(data)
    alerts_enriched = build_alerts_enriched(data)
    done("Data normalized")
    print()

    # ---- Metrics ----
    volume = alert_volume_by_soc(alerts_enriched)
    severity_mix = severity_mix_by_soc(alerts_enriched)
    tp_rate = true_positive_rate_by_soc(alerts_enriched)
    categories = category_coverage_by_soc(alerts_enriched)
    done("Alert metrics calculated")

    workload = analyst_workload(alerts_enriched)
    workload_flagged = flag_overloaded_analysts(workload)
    done("Analyst metrics calculated")

    resolution_time = case_resolution_time_by_soc(data["cases"])
    reopen_rate = reopen_rate_by_soc(alerts_enriched)
    resolution_mix = resolution_outcome_mix_by_soc(data["cases"])
    done("Case metrics calculated")

    escalation_compliance = escalation_compliance_by_soc(alerts_enriched)
    done("Escalation metrics calculated")
    print()

    # ---- Detection ----
    exec_gap_findings = run_all_execution_gap_detectors(alerts_enriched, data["cases"], cfg)
    done("Execution gaps detected")

    neg_space_findings = run_all_negative_space_detectors(data, volume, categories, cfg)
    done("Negative-space findings detected")
    print()

    # ---- Scoring / findings assembly ----
    finding_counts = build_finding_counts(exec_gap_findings, neg_space_findings)
    risk_scores = compute_entity_risk_scores(finding_counts, volume, cfg)
    socs_with_group = add_peer_group(data["socs"], cfg)
    risk_scores = risk_scores.merge(
        socs_with_group[["soc_id", "organization_name"]], on="soc_id", how="left"
    )
    risk_scores = percentile_rank_by_peer_group(risk_scores, socs_with_group, "supervisory_risk_score")

    total_findings = len(exec_gap_findings) + len(neg_space_findings)
    done("Findings generated")
    print()

    # ---- Assemble entity-level assessments ----
    entity_assessments = []
    for soc_id in risk_scores["soc_id"]:
        breakdown = score_breakdown_for_entity(risk_scores, soc_id, cfg)
        row = risk_scores[risk_scores["soc_id"] == soc_id].iloc[0]

        entity_exec_findings = (exec_gap_findings[exec_gap_findings["soc_id"] == soc_id]
                                 if not exec_gap_findings.empty else pd.DataFrame())
        entity_neg_findings = (neg_space_findings[neg_space_findings["soc_id"] == soc_id]
                                if not neg_space_findings.empty else pd.DataFrame())

        entity_assessments.append({
            "soc_id": soc_id,
            "organization_name": row.get("organization_name"),
            "peer_group": row.get("peer_group"),
            "priority_rank": int(row["priority_rank"]),
            "supervisory_risk_score": round(float(row["supervisory_risk_score"]), 3),
            "supervisory_risk_score_percentile": round(float(row.get("supervisory_risk_score_percentile", 0)), 1),
            "execution_gap_count": len(entity_exec_findings),
            "negative_space_count": len(entity_neg_findings),
            "score_breakdown": breakdown.get("components", []),
            "execution_gap_findings": df_records(entity_exec_findings),
            "negative_space_findings": df_records(entity_neg_findings),
            "metrics": {
                "alert_volume": df_records(volume[volume["soc_id"] == soc_id]),
                "severity_mix": df_records(severity_mix[severity_mix["soc_id"] == soc_id]),
                "true_positive_rate": df_records(tp_rate[tp_rate["soc_id"] == soc_id]),
                "analyst_workload": df_records(workload_flagged[workload_flagged["soc_id"] == soc_id]),
                "case_resolution_time": df_records(resolution_time[resolution_time["soc_id"] == soc_id]),
                "reopen_rate": df_records(reopen_rate[reopen_rate["soc_id"] == soc_id]),
                "resolution_outcome_mix": df_records(resolution_mix[resolution_mix["soc_id"] == soc_id]),
                "escalation_compliance": df_records(
                    escalation_compliance[escalation_compliance["soc_id"] == soc_id]
                    if not escalation_compliance.empty else pd.DataFrame()
                ),
            },
        })

    assessment_results = {
        "run_metadata": {
            "data_path": data_path,
            "entities_assessed": len(risk_scores),
            "total_alerts": int(len(alerts_enriched)),
            "total_findings": int(total_findings),
            "pipeline_steps_completed": steps_completed,
        },
        "validation_report": {
            "issue_count": len(validation_report.issues),
            "issues": [
                {"table": i.table, "severity": i.severity, "message": i.message, "row_count": i.row_count}
                for i in validation_report.issues
            ],
        },
        "entities": sorted(entity_assessments, key=lambda e: e["priority_rank"]),
    }

    os.makedirs(out_path, exist_ok=True)
    out_file = os.path.join(out_path, "assessment_results.json")
    with open(out_file, "w") as f:
        json.dump(assessment_results, f, indent=2, default=str)

    print("Assessment complete.")
    print(f"\nOutput written to: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="SAT-SA Analytics Engine v0.1")
    parser.add_argument("--data", default="./data/synthetic", help="Path to input dataset directory")
    parser.add_argument("--out", default="outputs", help="Path to write results")
    parser.add_argument("--config", default="config/assessment_rules.yaml", help="Path to rules config")
    args = parser.parse_args()

    run_pipeline(args.data, args.out, args.config)


if __name__ == "__main__":
    main()
