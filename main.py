#!/usr/bin/env python3
"""
SAT-SA analytics engine — end-to-end pipeline.

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

from analytics.ingestion import IngestionError
from analytics.validation import GROUND_TRUTH_FILE
from analytics.loader import load_soc_dataset
from analytics.validator import validate_dataset, DatasetValidationError
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
from analytics.completeness import assess_completeness
from analytics.detection.negative_space import run_all_negative_space_detectors
from analytics.scoring.benchmark import add_peer_group, percentile_rank_by_peer_group
from analytics.scoring.score import build_finding_counts, compute_entity_risk_scores, score_breakdown_for_entity
from analytics.review_queue import build_review_queue
from analytics.llm_narration import narrate_queue
from analytics.narration import resolve_backend_name
from analytics.reporting import write_csv_exports, write_pdf_report

# Version only. A constants module with no imports of its own, so the
# headless engine keeps working on a core-only install with no PySide6.
from application.version import APP_VERSION
from analytics.trends import build_trends
from analytics.validation import (
    format_report,
    load_ground_truth,
    validate,
)


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def df_records(df: pd.DataFrame) -> list:
    """DataFrame -> list of JSON-safe dicts (NaN/NaT -> None)."""
    if df is None or df.empty:
        return []
    clean = df.astype(object).where(pd.notnull(df), None)
    return clean.to_dict(orient="records")


def run_validation(data_path: str, out_path: str, config_path: str):
    """
    Assess a dataset and measure the result against its seeded ground
    truth.

    Only possible where the dataset carries labels. A real submission
    does not, and the absence of labels means "cannot validate" — never
    "nothing was wrong".
    """
    ground_truth = load_ground_truth(data_path)
    if ground_truth is None:
        raise ValueError(
            f"No {GROUND_TRUTH_FILE} beside {data_path}.\n\n"
            "Validation measures detection against conditions known to "
            "have been injected. A submission without labels cannot be "
            "validated — which says nothing about whether it contains "
            "findings. Generate a labelled dataset with "
            "data/generator/generate_dataset.py.")

    cfg = load_config(config_path)
    results = run_pipeline(data_path, out_path, config_path)

    data = load_soc_dataset(data_path)
    report = validate(results, ground_truth, dataset=data_path,
                       alerts=data.get("alerts"), config=cfg)

    text = format_report(report)
    print()
    print(text)

    os.makedirs(out_path, exist_ok=True)
    report_path = os.path.join(out_path, "validation_report.txt")
    with open(report_path, "w") as handle:
        handle.write(text + "\n")
    print(f"Validation report written to: {report_path}")

    return report


def detect_periods(data_path: str) -> list:
    """
    Period subdirectories inside a dataset folder, oldest first.

    A folder is multi-period when its subdirectories each look like a
    dataset in their own right — that is, each contains alerts.csv or
    alerts.json. Anything else (an outputs folder, a stray directory) is
    ignored rather than mistaken for a period.

    Returns [] for a single-period dataset, so the caller can treat the
    ordinary case as the default.
    """
    if not os.path.isdir(data_path):
        return []

    periods = []
    for name in sorted(os.listdir(data_path)):
        candidate = os.path.join(data_path, name)
        if not os.path.isdir(candidate):
            continue
        if any(os.path.exists(os.path.join(candidate, f"alerts{ext}"))
               for ext in (".csv", ".json")):
            periods.append((name, candidate))

    return periods


def run_multi_period_pipeline(data_path: str, out_path: str, config_path: str,
                                export_csv: bool = False,
                                export_pdf: bool = False,
                                narrate: bool = False,
                                progress_callback=None):
    """
    Assess each submission period independently, then compare them.

    Each period is a full, self-contained assessment. That is what makes
    the comparison meaningful: risk scores normalise per 100 alerts and
    several rules are z-scores against peers, so assessing periods
    together rather than separately would corrupt every number the trend
    is drawn from.

    The LATEST period's assessment is written to `out_path` as the
    current assessment, so every existing consumer keeps working
    unchanged; per-period outputs go to `out_path/periods/<label>/` and
    the comparison to `out_path/trend_report.json`.
    """
    periods = detect_periods(data_path)
    if len(periods) < 2:
        raise ValueError(
            f"{data_path} does not contain multiple period subdirectories.")

    period_results = []
    for label, path in periods:
        if progress_callback:
            progress_callback(f"Assessing period {label}...")
        print(f"\n=== Period {label} ===")

        results = run_pipeline(
            path, os.path.join(out_path, "periods", label), config_path,
            export_csv=export_csv, export_pdf=export_pdf, narrate=narrate)
        period_results.append((label, results))

    report = build_trends(period_results)

    # The combined report covers every period, so it is written beside
    # the current assessment rather than inside any one period.
    if export_pdf:
        write_pdf_report(out_path, period_results[-1][1],
                          load_config(config_path), trend_report=report)

    os.makedirs(out_path, exist_ok=True)

    # The newest period becomes the current assessment.
    latest_label, latest = period_results[-1]
    latest = dict(latest)
    latest["run_metadata"] = {
        **latest.get("run_metadata", {}),
        "submission_periods": len(periods),
        "submission_period_labels": [label for label, _ in periods],
        "current_period": latest_label,
    }
    with open(os.path.join(out_path, "assessment_results.json"), "w") as f:
        json.dump(latest, f, indent=2, default=str)

    with open(os.path.join(out_path, "trend_report.json"), "w") as f:
        json.dump(trend_report_to_dict(report), f, indent=2, default=str)

    if progress_callback:
        progress_callback(f"Trend analysis across {len(periods)} periods")

    print(f"\n{report.summary()}")
    print(f"Trend report written to: "
          f"{os.path.join(out_path, 'trend_report.json')}")

    return latest, report


def trend_report_to_dict(report) -> dict:
    """Serialisable form of a TrendReport, for the JSON output."""
    def series_map(mapping):
        return {soc: {"periods": s.periods, "values": s.values,
                       "change": s.change, "direction": s.direction}
                for soc, s in mapping.items()}

    return {
        "periods": report.periods,
        "available": report.available,
        "message": report.message,
        "summary": report.summary(),
        "risk_score": series_map(report.risk_score),
        "total_findings": series_map(report.total_findings),
        "execution_gaps": series_map(report.execution_gaps),
        "negative_space": series_map(report.negative_space),
        "portfolio": series_map(report.portfolio),
        "rank_changes": [
            {"soc_id": r.soc_id, "first_rank": r.first_rank,
             "last_rank": r.last_rank, "change": r.change, "label": r.label}
            for r in report.rank_changes
        ],
        "repeated_findings": [
            {"soc_id": r.soc_id, "finding_type": r.finding_type,
             "periods_seen": r.periods_seen, "counts": r.counts,
             "period_count": r.period_count}
            for r in report.repeated_findings
        ],
    }


def validate_dataset_at(data_path: str, limits=None):
    """
    Load and validate a dataset WITHOUT running any analytics.

    This is the "AUTOMATIC DATA VALIDATION -> SHOW VALIDATION RESULT"
    step the supervisory workflow requires before an assessment starts.
    Returns the ValidationReport; never raises on validation content
    (only on an unreadable dataset), so the caller decides what to do
    with errors.
    """
    data = (load_soc_dataset(data_path, limits) if limits is not None
            else load_soc_dataset(data_path))
    return validate_dataset(data)


def run_pipeline(
    data_path: str,
    out_path: str,
    config_path: str,
    export_csv: bool = False,
    export_pdf: bool = False,
    narrate: bool = False,
    progress_callback=None,
    strict_validation: bool = False,
    limits=None,
):
    cfg = load_config(config_path)
    steps_completed = []

    def done(label):
        print(f"✓ {label}")
        steps_completed.append(label)

        if progress_callback:
            progress_callback(label)

    # ---- Load ----
    data = (load_soc_dataset(data_path, limits) if limits is not None
            else load_soc_dataset(data_path))
    done("Dataset loaded")

    # ---- Validate ----
    # A dataset that fails ERROR-level validation raises rather than
    # returning None. The old `return` left every caller unable to tell
    # "validation rejected this" from "the run produced nothing", and
    # the desktop UI surfaced it as an opaque RuntimeError with the
    # actual reasons stranded in stdout.
    validation_report = validate_dataset(data)
    # Warnings do not block by default: the validator's contract is to
    # report everything wrong so a supervisor can send one complete list
    # of corrections back, and a warning rarely makes a submission
    # unanalysable. A supervisor who wants a stricter gate sets it.
    blocked = (validation_report.issues if strict_validation
               else validation_report.errors)
    if blocked:
        raise DatasetValidationError(validation_report, data_path)
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

    neg_space_findings = run_all_negative_space_detectors(data, volume, categories, cfg, alerts_enriched)
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

    # ---- Supervisory review queue ----
    review_queue = build_review_queue(exec_gap_findings, neg_space_findings, cfg)
    review_queue_records = df_records(review_queue)
    if narrate:
        cfg.setdefault("llm_narration", {})["enabled"] = True
        backend_name = resolve_backend_name(cfg)
        review_queue_records = narrate_queue(review_queue_records, cfg)
        explained = sum(1 for r in review_queue_records
                        if r.get("narration_source") not in (None, "rule"))
        # Report the backend that actually ran. Saying "offline Qwen"
        # when the deterministic sample explainer produced the text
        # would misstate how a supervisory explanation was generated.
        done(f"Review queue narrated via '{backend_name}' "
             f"({explained} of {len(review_queue_records)} items explained)")
    done("Supervisory review queue built")
    print()

    # ---- Assemble entity-level assessments ----
    # How much of each submission the checks could actually run against.
    # Reported ALONGSIDE the risk score and never folded into it: the
    # score counts what the records allow it to count, and a supervisor
    # has to be able to tell "little went wrong" from "little could be
    # examined". See analytics/completeness.py.
    completeness = assess_completeness(
        alerts_enriched, data.get("cases"), data.get("alert_events"),
        threshold=cfg.get("evidence_completeness", {}).get(
            "min_coverage_fraction", 0.5),
    )

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
            "evidence_completeness": (
                completeness[soc_id].to_dict() if soc_id in completeness
                else None),
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
        "validation_report": validation_report.to_dict(),
        "entities": sorted(entity_assessments, key=lambda e: e["priority_rank"]),
        "review_queue": review_queue_records,
    }

    os.makedirs(out_path, exist_ok=True)
    out_file = os.path.join(out_path, "assessment_results.json")
    with open(out_file, "w") as f:
        json.dump(assessment_results, f, indent=2, default=str)

    print("Assessment complete.")
    print(f"\nOutput written to: {out_file}")

    if export_csv:
        csv_files = write_csv_exports(out_path, risk_scores, exec_gap_findings,
                                       neg_space_findings, review_queue)
        done("CSV exports written")
        for f in csv_files:
            print(f"  {f}")

    if export_pdf:
        pdf_file = write_pdf_report(out_path, assessment_results, cfg)
        done("PDF executive summary written")
        print(f"  {pdf_file}")

    return assessment_results


def main():
    parser = argparse.ArgumentParser(
        description=f"SAT-SA analytics engine v{APP_VERSION} — headless assessment pipeline.")
    parser.add_argument("--data", default="./data/synthetic", help="Path to input dataset directory")
    parser.add_argument("--out", default="outputs", help="Path to write results")
    parser.add_argument("--config", default="config/assessment_rules.yaml", help="Path to rules config")
    parser.add_argument("--export-csv", action="store_true", help="Also write flat CSV exports")
    parser.add_argument("--export-pdf", action="store_true", help="Also write a PDF executive summary")
    parser.add_argument("--validate", action="store_true",
                         help="Measure detection against the dataset's "
                              "seeded ground truth (synthetic datasets "
                              "only) and write a validation report")
    parser.add_argument("--trends", action="store_true",
                         help="Assess every period subdirectory under --data "
                              "independently and write a trend comparison")
    parser.add_argument("--narrate", action="store_true",
                         help="Narrate the review queue via the offline Qwen layer "
                              "(requires llm_narration.enabled: true in config and a running Ollama instance)")
    args = parser.parse_args()

    try:
        if args.validate:
            run_validation(args.data, args.out, args.config)
        elif args.trends:
            run_multi_period_pipeline(
                args.data, args.out, args.config,
                export_csv=args.export_csv, export_pdf=args.export_pdf,
                narrate=args.narrate)
        else:
            run_pipeline(args.data, args.out, args.config,
                         export_csv=args.export_csv,
                         export_pdf=args.export_pdf,
                         narrate=args.narrate)
    except DatasetValidationError as exc:
        print(exc.message())
        raise SystemExit(1)
    except IngestionError as exc:
        print(exc.message())
        raise SystemExit(1)
    except ValueError as exc:
        # Raised by --validate on an unlabelled dataset and by --trends
        # on a single-period one. Both are usage answers, not crashes.
        print(exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
