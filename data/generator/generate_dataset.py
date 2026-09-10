#!/usr/bin/env python3
"""
Synthetic SOC dataset generator for SAT-SA.

Produces all 13 CSV tables the loader/validator expect, with a seeded
"risk profile" per SOC that controls how often each detection rule
fires — so the dataset comes with reproducible ground truth instead
of being an unexplainable pile of random numbers.

Risk profiles:
    clean    - low rates of every gap; a well-run SOC
    typical  - moderate, realistic rates across the board
    weak     - elevated rates on most execution-gap rules
    low_activity - deliberately sparse alert volume (exercises the
                    LOW_ACTIVITY_OUTLIER negative-space rule)
    poor_recordkeeping - alerts/cases exist but escalation and
                    investigation records are largely absent
                    (exercises MISSING_ESCALATION_RECORDS and
                    MISSING_INVESTIGATIONS)

Usage:
    python data/generator/generate_dataset.py --socs 5 --alerts-per-soc 800 --out data/synthetic
"""

import argparse
import os
import random
from datetime import datetime, timedelta, timezone

import pandas as pd

SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SEVERITY_WEIGHTS = [0.45, 0.30, 0.18, 0.07]

CATEGORIES = [
    "MALWARE", "PHISHING", "BRUTE_FORCE", "DATA_EXFIL", "LATERAL_MOVEMENT",
    "PRIVILEGE_ESCALATION", "C2_COMMUNICATION", "POLICY_VIOLATION", "RECON",
]

SOURCES = ["SIEM", "EDR", "IDS", "FIREWALL", "CLOUD_TRAIL", "DLP"]
SECTORS = ["FINANCE", "ENERGY", "HEALTHCARE", "GOVERNMENT", "TELECOM", "MANUFACTURING"]
ANALYST_ROLES = ["TIER1", "TIER2", "TIER3", "SUPERVISOR"]

MITRE = [
    ("T1566", "Phishing", "Initial Access", "Adversary sends malicious attachments/links."),
    ("T1110", "Brute Force", "Credential Access", "Adversary attempts credential guessing."),
    ("T1071", "Application Layer Protocol", "Command and Control", "C2 over common protocols."),
    ("T1021", "Remote Services", "Lateral Movement", "Adversary uses valid accounts on remote services."),
    ("T1041", "Exfiltration Over C2 Channel", "Exfiltration", "Data stolen over existing C2 channel."),
    ("T1068", "Exploitation for Privilege Escalation", "Privilege Escalation", "Exploits software vulnerabilities."),
    ("T1059", "Command and Scripting Interpreter", "Execution", "Abuse of command/script interpreters."),
]

RESOLUTION_REASONS = [
    "True positive - remediated", "False positive - benign activity",
    "True positive - contained", "Duplicate of existing case",
    "Benign - authorized activity",
]

TEMPLATED_NOTES = [
    "Reviewed and closed.", "Investigated, no further action required.",
    "Alert reviewed per SOP, closed as benign.",
]

RISK_PROFILES = {
    # (slow_triage_rate, fast_closure_rate, missing_evidence_rate,
    #  reopen_rate, missed_escalation_rate, template_note_rate,
    #  overload_skew, telemetry_health_rate, alert_volume_multiplier,
    #  escalation_recordkeeping_rate, investigation_note_rate)
    "clean": dict(slow_triage=0.03, fast_closure=0.02, missing_evidence=0.03,
                  reopen=0.02, missed_escalation=0.02, template_notes=0.05,
                  overload_skew=1.0, telemetry_health=0.97, volume_mult=1.0,
                  escalation_records=0.98, investigation_notes=0.97),
    "typical": dict(slow_triage=0.12, fast_closure=0.08, missing_evidence=0.12,
                     reopen=0.08, missed_escalation=0.10, template_notes=0.15,
                     overload_skew=1.4, telemetry_health=0.85, volume_mult=1.0,
                     escalation_records=0.90, investigation_notes=0.85),
    "weak": dict(slow_triage=0.35, fast_closure=0.28, missing_evidence=0.30,
                 reopen=0.22, missed_escalation=0.30, template_notes=0.35,
                 overload_skew=3.0, telemetry_health=0.55, volume_mult=1.0,
                 escalation_records=0.75, investigation_notes=0.60),
    "low_activity": dict(slow_triage=0.10, fast_closure=0.08, missing_evidence=0.10,
                          reopen=0.06, missed_escalation=0.08, template_notes=0.12,
                          overload_skew=1.2, telemetry_health=0.80, volume_mult=0.12,
                          escalation_records=0.88, investigation_notes=0.85),
    "poor_recordkeeping": dict(slow_triage=0.15, fast_closure=0.10, missing_evidence=0.20,
                                reopen=0.10, missed_escalation=0.15, template_notes=0.20,
                                overload_skew=1.5, telemetry_health=0.80, volume_mult=1.0,
                                escalation_records=0.15, investigation_notes=0.20),
}

PROFILE_ORDER = ["clean", "typical", "weak", "low_activity", "poor_recordkeeping"]


def utc_now_minus(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def build_dataset(n_socs: int, alerts_per_soc: int, seed: int) -> dict:
    random.seed(seed)
    period_start = utc_now_minus(90)
    period_end = utc_now_minus(0)

    socs, analysts, shifts = [], [], []
    alerts, alert_events, cases, escalations = [], [], [], []
    actions, evidence, incidents, telemetry, policies = [], [], [], [], []

    profiles_assigned = []
    for i in range(n_socs):
        profile = PROFILE_ORDER[i] if i < len(PROFILE_ORDER) else random.choice(PROFILE_ORDER[:3])
        profiles_assigned.append(profile)

    for i, profile in enumerate(profiles_assigned):
        soc_id = f"SOC-{i+1:03d}"
        cfg = RISK_PROFILES[profile]
        sector = random.choice(SECTORS)
        n_analysts = random.randint(6, 14)

        socs.append({
            "soc_id": soc_id,
            "organization_name": f"{sector.title()} CSE {i+1:03d}",
            "sector": sector,
            "organization_size": random.choice(["SMALL", "MEDIUM", "LARGE"]),
            "assessment_period_start": period_start.isoformat(),
            "assessment_period_end": period_end.isoformat(),
            "soc_maturity_level": {"clean": 4, "typical": 3, "weak": 2,
                                    "low_activity": 2, "poor_recordkeeping": 2}[profile],
            "analyst_count": n_analysts,
            "shift_count": 3,
            "criticality": random.choice(["HIGH", "MEDIUM", "LOW"]),
        })

        soc_analyst_ids = []
        for a in range(n_analysts):
            analyst_id = f"ANL-{soc_id}-{a+1:03d}"
            soc_analyst_ids.append(analyst_id)
            analysts.append({
                "analyst_id": analyst_id, "soc_id": soc_id,
                "role": random.choice(ANALYST_ROLES),
                "experience_years": round(random.uniform(0.5, 12), 1),
                "shift_id": f"SHIFT-{soc_id}-{(a % 3) + 1}",
                "active": True,
            })

        for s in range(3):
            shifts.append({
                "shift_id": f"SHIFT-{soc_id}-{s+1}", "soc_id": soc_id,
                "shift_date": period_end.date().isoformat(),
                "start_time": f"{(s*8):02d}:00", "end_time": f"{((s+1)*8) % 24:02d}:00",
                "shift_type": ["DAY", "SWING", "NIGHT"][s],
                "analyst_count": max(1, n_analysts // 3),
                "supervisor_id": random.choice(soc_analyst_ids),
            })

        # skew alert assignment so one analyst is a workload outlier on
        # weak/typical profiles (feeds ANALYST_OVERLOAD)
        weights = [1.0] * len(soc_analyst_ids)
        if cfg["overload_skew"] > 1.0 and len(soc_analyst_ids) > 1:
            weights[0] = cfg["overload_skew"] * len(soc_analyst_ids)

        n_alerts = max(5, int(alerts_per_soc * cfg["volume_mult"]))

        for a_idx in range(n_alerts):
            alert_id = f"ALT-{soc_id}-{a_idx+1:06d}"
            created = period_start + timedelta(
                seconds=random.uniform(0, (period_end - period_start).total_seconds())
            )
            severity = random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS, k=1)[0]
            assigned_analyst = random.choices(soc_analyst_ids, weights=weights, k=1)[0]
            category = random.choice(CATEGORIES)
            mitre = random.choice(MITRE)
            status = random.choices(
                ["CLOSED", "OPEN", "IN_PROGRESS"], weights=[0.85, 0.05, 0.10], k=1
            )[0]

            alerts.append({
                "alert_id": alert_id, "soc_id": soc_id,
                "timestamp_created": created.isoformat(),
                "source": random.choice(SOURCES), "source_system": f"{random.choice(SOURCES)}-01",
                "alert_type": category, "category": category, "severity": severity,
                "risk_score": round(random.uniform(1, 100), 1),
                "asset_id": f"ASSET-{random.randint(1, 500):04d}",
                "user_id": f"USER-{random.randint(1, 2000):05d}",
                "source_ip": f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
                "destination_ip": f"172.{random.randint(16,31)}.{random.randint(0,255)}.{random.randint(1,254)}",
                "mitre_tactic": mitre[2], "mitre_technique": mitre[0],
                "true_positive": random.random() < 0.6,
                "assigned_analyst_id": assigned_analyst, "status": status,
                "priority": severity, "scenario_type": profile,
            })

            # --- lifecycle events ---
            ack_target_minutes = {"LOW": 60, "MEDIUM": 30, "HIGH": 15, "CRITICAL": 10}[severity]
            is_slow = random.random() < cfg["slow_triage"]
            ack_delay = ack_target_minutes * random.uniform(3, 6) if is_slow else ack_target_minutes * random.uniform(0.1, 0.9)
            acked = created + timedelta(minutes=ack_delay)

            alert_events.append({"event_id": f"EVT-{alert_id}-1", "alert_id": alert_id,
                                  "event_type": "CREATED", "timestamp": created.isoformat(),
                                  "actor_id": "SYSTEM", "previous_status": None, "new_status": "OPEN"})
            alert_events.append({"event_id": f"EVT-{alert_id}-2", "alert_id": alert_id,
                                  "event_type": "ACKNOWLEDGED", "timestamp": acked.isoformat(),
                                  "actor_id": assigned_analyst, "previous_status": "OPEN",
                                  "new_status": "IN_PROGRESS"})

            invest_start = acked + timedelta(minutes=random.uniform(1, 5))
            triage_sla = {"LOW": 480, "MEDIUM": 240, "HIGH": 90, "CRITICAL": 45}[severity]
            is_fast = severity in ("HIGH", "CRITICAL") and random.random() < cfg["fast_closure"]
            if is_fast:
                duration = random.uniform(1, triage_sla * 0.10)
            else:
                duration = random.uniform(triage_sla * 0.3, triage_sla * 1.5)
            closed = invest_start + timedelta(minutes=duration)

            if status == "CLOSED":
                alert_events.append({"event_id": f"EVT-{alert_id}-3", "alert_id": alert_id,
                                      "event_type": "INVESTIGATION_STARTED", "timestamp": invest_start.isoformat(),
                                      "actor_id": assigned_analyst, "previous_status": "IN_PROGRESS",
                                      "new_status": "IN_PROGRESS"})
                alert_events.append({"event_id": f"EVT-{alert_id}-4", "alert_id": alert_id,
                                      "event_type": "CLOSED", "timestamp": closed.isoformat(),
                                      "actor_id": assigned_analyst, "previous_status": "IN_PROGRESS",
                                      "new_status": "CLOSED"})
                was_reopened = random.random() < cfg["reopen"]
                if was_reopened:
                    reopened_at = closed + timedelta(hours=random.uniform(1, 72))
                    alert_events.append({"event_id": f"EVT-{alert_id}-5", "alert_id": alert_id,
                                          "event_type": "REOPENED", "timestamp": reopened_at.isoformat(),
                                          "actor_id": assigned_analyst, "previous_status": "CLOSED",
                                          "new_status": "IN_PROGRESS"})

            # --- case ---
            case_id = f"CASE-{alert_id}"
            has_investigation_note = random.random() < cfg["investigation_notes"]
            if not has_investigation_note:
                note = ""
            elif random.random() < cfg["template_notes"]:
                note = random.choice(TEMPLATED_NOTES)
            else:
                note = f"Investigated {category.lower().replace('_', ' ')} on {alerts[-1]['asset_id']}: {random.choice(RESOLUTION_REASONS)}."

            cases.append({
                "case_id": case_id, "soc_id": soc_id, "incident_id": None,
                "alert_id": alert_id, "created_at": created.isoformat(),
                "assigned_analyst_id": assigned_analyst, "severity": severity,
                "status": status, "resolution": "CLOSED" if status == "CLOSED" else "OPEN",
                "resolution_reason": random.choice(RESOLUTION_REASONS) if status == "CLOSED" else None,
                "investigation_notes": note,
                "closed_at": closed.isoformat() if status == "CLOSED" else None,
            })

            # --- escalation ---
            escalation_required = severity in ("HIGH", "CRITICAL")
            has_escalation_record = random.random() < cfg["escalation_records"]
            if escalation_required and has_escalation_record:
                initiated = random.random() >= cfg["missed_escalation"]
                escalations.append({
                    "escalation_id": f"ESC-{alert_id}", "alert_id": alert_id, "case_id": case_id,
                    "required": True, "required_level": "TIER2" if severity == "HIGH" else "TIER3",
                    "trigger_reason": f"{severity} severity per policy",
                    "initiated": initiated,
                    "initiated_at": (created + timedelta(minutes=random.uniform(5, 60))).isoformat() if initiated else None,
                    "initiated_by": assigned_analyst if initiated else None,
                    "target_team": "IR_TEAM", "completed": initiated,
                    "completed_at": (created + timedelta(minutes=random.uniform(60, 180))).isoformat() if initiated else None,
                    "delay_minutes": round(random.uniform(2, 40), 1) if initiated else None,
                })
            elif not escalation_required and has_escalation_record and random.random() < 0.3:
                escalations.append({
                    "escalation_id": f"ESC-{alert_id}", "alert_id": alert_id, "case_id": case_id,
                    "required": False, "required_level": None, "trigger_reason": None,
                    "initiated": False, "initiated_at": None, "initiated_by": None,
                    "target_team": None, "completed": False, "completed_at": None,
                    "delay_minutes": None,
                })

            # --- evidence ---
            has_evidence = random.random() >= cfg["missing_evidence"] if severity in ("HIGH", "CRITICAL") else True
            if has_evidence:
                evidence.append({
                    "evidence_id": f"EVID-{alert_id}", "alert_id": alert_id, "case_id": case_id,
                    "analyst_id": assigned_analyst, "timestamp": closed.isoformat(),
                    "evidence_type": random.choice(["LOG_EXPORT", "PCAP", "SCREENSHOT", "MEMORY_DUMP"]),
                    "source": random.choice(SOURCES), "reference": f"s3://evidence/{alert_id}.bin",
                    "relevance": "PRIMARY",
                })

            # --- actions ---
            actions.append({
                "action_id": f"ACT-{alert_id}-1", "alert_id": alert_id, "analyst_id": assigned_analyst,
                "timestamp": acked.isoformat(), "action_type": "TRIAGE",
                "action_result": "ACKNOWLEDGED", "notes": "",
            })

        # --- incidents (rollup of a subset of critical alerts) ---
        n_incidents = max(1, n_alerts // 150)
        for inc_idx in range(n_incidents):
            created_inc = period_start + timedelta(
                seconds=random.uniform(0, (period_end - period_start).total_seconds())
            )
            incidents.append({
                "incident_id": f"INC-{soc_id}-{inc_idx+1:04d}", "soc_id": soc_id,
                "created_at": created_inc.isoformat(), "severity": random.choice(["HIGH", "CRITICAL"]),
                "incident_type": random.choice(CATEGORIES),
                "attack_stage": random.choice([m[2] for m in MITRE]),
                "affected_asset_count": random.randint(1, 20),
                "affected_user_count": random.randint(1, 50),
                "status": "CONTAINED",
                "contained_at": (created_inc + timedelta(hours=random.uniform(1, 24))).isoformat(),
                "resolved_at": (created_inc + timedelta(hours=random.uniform(24, 72))).isoformat(),
            })

        # --- telemetry ---
        expected_sources = ["EDR", "FIREWALL", "DNS", "PROXY", "IDENTITY", "CLOUD"]
        for src in expected_sources:
            healthy = random.random() < cfg["telemetry_health"]
            coverage = random.uniform(75, 99.5) if healthy else random.uniform(0, 60)
            telemetry.append({
                "telemetry_id": f"TEL-{soc_id}-{src}", "soc_id": soc_id, "source_name": src,
                "source_type": src, "expected": True, "enabled": healthy,
                "last_event_timestamp": period_end.isoformat() if healthy else (period_end - timedelta(days=10)).isoformat(),
                "event_count": random.randint(1000, 500000) if healthy else random.randint(0, 500),
                "coverage_percentage": round(coverage, 1),
                "health_status": "HEALTHY" if coverage >= 70 else ("DEGRADED" if coverage >= 30 else "MISSING"),
            })

        # --- policies ---
        for idx, (sev, action) in enumerate([("HIGH", "ESCALATE_TO_TIER2"), ("CRITICAL", "ESCALATE_TO_TIER3")]):
            policies.append({
                "policy_id": f"POL-{soc_id}-{idx+1}", "soc_id": soc_id,
                "rule_id": f"ESC-REQUIRED-00{idx+1}", "rule_name": f"{sev} escalation required",
                "severity": sev, "condition": f"severity == {sev}",
                "required_action": action, "threshold": "N/A",
            })

    mitre_techniques = [{
        "mitre_technique_id": m[0], "technique_name": m[1], "tactic": m[2], "description": m[3],
    } for m in MITRE]

    return {
        "socs": pd.DataFrame(socs), "analysts": pd.DataFrame(analysts), "shifts": pd.DataFrame(shifts),
        "alerts": pd.DataFrame(alerts), "alert_events": pd.DataFrame(alert_events),
        "cases": pd.DataFrame(cases), "escalations": pd.DataFrame(escalations),
        "actions": pd.DataFrame(actions), "evidence": pd.DataFrame(evidence),
        "incidents": pd.DataFrame(incidents), "telemetry": pd.DataFrame(telemetry),
        "policies": pd.DataFrame(policies), "mitre_techniques": pd.DataFrame(mitre_techniques),
    }, profiles_assigned


def main():
    parser = argparse.ArgumentParser(description="SAT-SA synthetic dataset generator")
    parser.add_argument("--socs", type=int, default=5, help="Number of SOC entities to generate")
    parser.add_argument("--alerts-per-soc", type=int, default=800, help="Approx. alerts per SOC (before profile volume multiplier)")
    parser.add_argument("--out", default="data/synthetic", help="Output directory for CSVs")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    tables, profiles = build_dataset(args.socs, args.alerts_per_soc, args.seed)

    os.makedirs(args.out, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(os.path.join(args.out, f"{name}.csv"), index=False)

    total_alerts = len(tables["alerts"])
    print(f"Generated {args.socs} SOCs, {total_alerts} alerts -> {args.out}")
    print("Seeded risk profiles (ground truth):")
    for i, profile in enumerate(profiles):
        print(f"  SOC-{i+1:03d}: {profile}")


if __name__ == "__main__":
    main()
