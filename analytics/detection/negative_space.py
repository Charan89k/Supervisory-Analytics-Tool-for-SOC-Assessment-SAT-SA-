"""
Negative space detection for SAT-SA.

Negative space is evidence that SHOULD exist but is absent — missing
telemetry, missing alert categories relative to peers, or unexpectedly
low activity for an entity's size/criticality. This is fundamentally
different from execution-gap detection: there is no row to point to,
only an absence, so every finding here must explain what was expected
and why.
"""

import math

import pandas as pd

from analytics.benchmarking import MIN_PEERS
from analytics.metrics.telemetry_metrics import expected_source_gaps
from analytics.scoring.benchmark import add_peer_group

#: Peer group for entities whose submission carries no sector. They are
#: compared only with each other, never folded into a named sector.
UNKNOWN_PEER_GROUP = "UNSPECIFIED"


def minimum_group_for_zscore(threshold: float) -> int:
    """
    The smallest peer group in which a z-score threshold is reachable
    at all.

    A sample z-score is bounded by the group size. With n entities the
    most extreme value any one of them can reach is (n-1)/sqrt(n), so a
    group of 3 tops out at 1.155 and can NEVER satisfy a -1.5
    threshold — however under-instrumented an entity actually is.

    Without this, such a group looks assessed and silently reports
    nothing, which is the worst outcome: a supervisor reads "no low
    activity outliers" as evidence of health when the test could not
    have produced a finding in the first place. Deriving the minimum
    from the configured threshold keeps the two honest about each
    other, and changing the threshold changes the minimum with it.
    """
    limit = abs(float(threshold))
    size = max(2, MIN_PEERS)
    while (size - 1) / math.sqrt(size) < limit:
        size += 1
        if size > 1000:            # a threshold no real setting reaches
            break
    return size


def detect_telemetry_gaps(telemetry: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    min_coverage = cfg["negative_space"]["min_expected_coverage_pct"]
    gaps = expected_source_gaps(telemetry, min_coverage)

    if gaps.empty:
        return pd.DataFrame()

    gaps = gaps.copy()
    gaps["finding_type"] = "TELEMETRY_GAP"
    gaps["rule_id"] = "TELEMETRY-COVERAGE-001"
    gaps["rationale"] = (
        "Expected telemetry source '" + gaps["source_name"] + "' shows "
        + gaps["coverage_percentage"].round(1).astype(str)
        + "% coverage, below the required " + str(min_coverage) + "% threshold "
        "(status: " + gaps["health_status"] + ") — a potential monitoring "
        "blind spot for this entity."
    )
    gaps["evidence"] = gaps.apply(lambda r: {
        "source_name": r["source_name"],
        "coverage_percentage": r["coverage_percentage"],
        "required_coverage_percentage": min_coverage,
        "health_status": r["health_status"],
    }, axis=1)
    return gaps[["soc_id", "source_name", "finding_type", "rule_id", "rationale", "evidence"]]


def detect_missing_categories(alert_categories_by_soc: pd.DataFrame, cfg: dict,
                                peer_groups: dict = None) -> pd.DataFrame:
    """
    alert_categories_by_soc: output of alert_metrics.category_coverage_by_soc
    (soc_id -> set of categories observed).

    Flags an entity as missing a category if that category is present
    for a strong majority of its PEER GROUP but absent for this one.

    The comparison is peer-relative for the same reason
    LOW_ACTIVITY_OUTLIER is. "Expected" only means anything against
    comparable entities: an energy CSE not reporting a category that
    every healthcare CSE reports says nothing, because the two run
    different technology against different threats. Pooling sectors
    both invents gaps (a category normal for one sector, absent in
    another) and hides them (a category universal within a sector gets
    diluted below the threshold by sectors that do not use it).

    A group below MIN_PEERS is not assessed. "Present for a strong
    majority of peers" is not a claim two entities can support.
    """
    presence_fraction = cfg["negative_space"]["missing_category_peer_presence_fraction"]

    df = alert_categories_by_soc.copy()
    if df.empty:
        return pd.DataFrame()

    if peer_groups:
        df["peer_group"] = (df["soc_id"].map(peer_groups)
                            .fillna(UNKNOWN_PEER_GROUP)
                            .replace("", UNKNOWN_PEER_GROUP))
    else:
        df["peer_group"] = UNKNOWN_PEER_GROUP

    findings = []
    for group_name, group in df.groupby("peer_group", sort=True):
        if len(group) < MIN_PEERS:
            continue

        observed = set()
        for categories in group["categories_observed"]:
            observed |= set(categories)

        peers = len(group)
        for category in sorted(observed):
            present_mask = group["categories_observed"].apply(
                lambda seen: category in seen)
            peer_presence = present_mask.sum() / peers
            if peer_presence < presence_fraction:
                continue  # not common enough among peers to be "expected"

            where = ("its peer group" if group_name == UNKNOWN_PEER_GROUP
                     else f"the {group_name} peer group")

            for _, row in group[~present_mask].iterrows():
                findings.append({
                    "soc_id": row["soc_id"],
                    "category": category,
                    "finding_type": "MISSING_ALERT_CATEGORY",
                    "rule_id": "CATEGORY-PEER-COVERAGE-001",
                    "rationale": (
                        f"'{category}' alerts appear for "
                        f"{round(peer_presence * 100, 1)}% of entities in "
                        f"{where} ({peers} entities) but were not observed "
                        f"for this entity during the assessment period. "
                        f"Absence of a category may reflect a genuine "
                        f"monitoring blind spot or simply a different "
                        f"technology footprint — it is an indicator for "
                        f"review, not a defect on its own."
                    ),
                    "evidence": {
                        "missing_category": category,
                        "peer_group": group_name,
                        "peer_presence_fraction": round(peer_presence, 3),
                        "peer_presence_threshold": presence_fraction,
                        "peer_entity_count": int(peers),
                        "peers_with_category": int(present_mask.sum()),
                        "minimum_peers_required": MIN_PEERS,
                    },
                })
    return pd.DataFrame(findings)


def detect_low_activity_outliers(alert_volume_by_soc: pd.DataFrame, cfg: dict,
                                   peer_groups: dict = None) -> pd.DataFrame:
    """
    Flags entities whose alert volume is a low outlier RELATIVE TO THEIR
    PEER GROUP — a proxy for monitoring blind spots or
    under-instrumented environments.

    The baseline is the peer group, not the whole submission, and that
    distinction decides whether the rule works at all. Sectors differ in
    natural alert volume by an order of magnitude. Pooling them inflates
    the standard deviation until nothing is more than 1.5 sigma from a
    mean that describes no real population, so genuine blind spots are
    MASKED rather than over-reported.

    Measured on a mixed submission: a healthcare entity logging 150
    alerts against healthcare peers averaging ~910 scored z = -0.93
    pooled with energy entities (not flagged) versus z = -1.50 within
    its own sector (flagged). The pooled comparison hid exactly the
    entity the rule exists to find.

    Peer groups come from the same `add_peer_group` the benchmarking
    layer uses, so "peer" means one thing across the product and no
    sector list is hard-coded anywhere.

    A group too small for the configured threshold is not assessed at
    all — see `minimum_group_for_zscore`. A z-score is bounded by group
    size, so a 3-entity group cannot reach -1.5 no matter how extreme
    the entity, and pretending otherwise would let a supervisor read
    "nothing flagged" as evidence of health.
    """
    threshold = cfg["negative_space"]["low_activity_zscore_threshold"]

    df = alert_volume_by_soc.copy()
    if df.empty:
        return pd.DataFrame()

    if peer_groups:
        df["peer_group"] = (df["soc_id"].map(peer_groups)
                            .fillna(UNKNOWN_PEER_GROUP)
                            .replace("", UNKNOWN_PEER_GROUP))
    else:
        # No sector metadata supplied: one group, and the rationale says
        # so rather than implying a sector comparison that never happened.
        df["peer_group"] = UNKNOWN_PEER_GROUP

    minimum_group = minimum_group_for_zscore(threshold)

    findings = []
    for group_name, group in df.groupby("peer_group", sort=True):
        if len(group) < minimum_group:
            # Either too few peers for a standard deviation to describe
            # anything, or too few for this threshold to be reachable.
            # Saying nothing is the honest answer; see
            # minimum_group_for_zscore.
            continue

        mean = group["alert_count"].mean()
        std = group["alert_count"].std()
        if not std or pd.isna(std):
            continue  # every peer reported the same volume

        median = group["alert_count"].median()

        for _, row in group.iterrows():
            zscore = (row["alert_count"] - mean) / std
            if zscore > threshold:
                continue

            where = ("its peer group" if group_name == UNKNOWN_PEER_GROUP
                     else f"the {group_name} peer group")
            findings.append({
                "soc_id": row["soc_id"],
                "alert_count": int(row["alert_count"]),
                "finding_type": "LOW_ACTIVITY_OUTLIER",
                "rule_id": "VOLUME-ZSCORE-001",
                "rationale": (
                    f"Alert volume ({int(row['alert_count'])}) is "
                    f"{round(zscore, 2)} standard deviations below the mean "
                    f"for {where} ({len(group)} entities, mean "
                    f"{round(mean, 1)}), which may indicate a monitoring "
                    f"gap rather than genuinely low risk. Volume differs "
                    f"legitimately with size and technology footprint; this "
                    f"is an indicator for review, not a defect on its own."
                ),
                "evidence": {
                    "alert_count": int(row["alert_count"]),
                    "peer_group": group_name,
                    "peer_group_size": int(len(group)),
                    "peer_mean_alert_count": round(float(mean), 1),
                    "peer_median_alert_count": round(float(median), 1),
                    "volume_zscore": round(float(zscore), 2),
                    "zscore_threshold": threshold,
                    "minimum_peers_required": minimum_group,
                },
            })

    return pd.DataFrame(findings)


def detect_missing_escalation_records(alerts_enriched: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags entities whose HIGH/CRITICAL alerts largely carry NO
    escalation record of any kind (initiated or not).

    Deliberately distinct from MISSED_ESCALATION (an execution gap
    where a specific alert's escalation record shows it should have
    escalated but didn't): this rule catches the absence case — an
    entity that does not appear to keep escalation records reliably,
    which a per-alert rule can never surface because there is no row
    to flag.

    Measured as COVERAGE, not as total absence. The earlier version
    fired only when an entity had literally zero escalation rows,
    which meant it never fired on real data: an entity logging 50
    escalation records against 800 alerts — 6% coverage, clearly
    broken record-keeping — scored identically to a fully compliant
    one, because 50 is not 0. Coverage also degrades gracefully: an
    entity at 45% is flagged, an entity at 95% is not, and the
    evidence records the actual ratio so a supervisor can judge.
    """
    required_severities = cfg["escalation"]["required_severities"]
    coverage_threshold = cfg["negative_space"].get(
        "escalation_record_coverage_fraction", 0.5)
    min_alerts = cfg["negative_space"].get(
        "escalation_record_min_high_crit_alerts", 10)

    high_crit = alerts_enriched[alerts_enriched["severity"].isin(required_severities)]
    if high_crit.empty:
        return pd.DataFrame()

    findings = []
    for soc_id, group in high_crit.groupby("soc_id"):
        total = len(group)
        if total < min_alerts:
            continue  # too few alerts for coverage to mean anything

        with_records = int(group["escalation_required"].notna().sum())
        coverage = with_records / total if total else 0.0
        if coverage >= coverage_threshold:
            continue

        findings.append({
            "soc_id": soc_id,
            "finding_type": "MISSING_ESCALATION_RECORDS",
            "rule_id": "ESCALATION-RECORDKEEPING-001",
            "rationale": (
                f"Only {with_records} of {total} HIGH/CRITICAL alerts "
                f"({round(coverage * 100, 1)}%) for this entity carry an "
                f"escalation record of any kind, below the "
                f"{round(coverage_threshold * 100, 1)}% threshold — a potential "
                "gap in escalation record-keeping, distinct from any single "
                "alert failing to escalate. Where no record exists, whether "
                "escalation occurred cannot be determined either way."
            ),
            "evidence": {
                "high_critical_alert_count": total,
                "escalation_records_found": with_records,
                "escalation_record_coverage": round(coverage, 3),
                "coverage_threshold": coverage_threshold,
            },
        })
    return pd.DataFrame(findings)


def detect_missing_investigations(cases: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags entities where a strong majority of cases carry no
    investigation notes at all (as opposed to REPETITIVE_INVESTIGATION,
    which fires on duplicate-but-present notes). Absence of any note
    text is a negative-space signal — there's no record to point a
    per-case rule at.
    """
    if cases.empty or "investigation_notes" not in cases.columns:
        return pd.DataFrame()

    min_case_count = cfg["negative_space"].get("missing_investigation_min_cases", 5)
    empty_fraction_threshold = cfg["negative_space"].get("missing_investigation_empty_fraction", 0.5)

    findings = []
    for soc_id, group in cases.groupby("soc_id"):
        if len(group) < min_case_count:
            continue
        notes = group["investigation_notes"].fillna("").astype(str).str.strip()
        empty_fraction = (notes == "").mean()
        if empty_fraction < empty_fraction_threshold:
            continue
        findings.append({
            "soc_id": soc_id,
            "finding_type": "MISSING_INVESTIGATIONS",
            "rule_id": "INVESTIGATION-RECORDKEEPING-001",
            "rationale": (
                f"{round(empty_fraction * 100, 1)}% of cases for this entity "
                f"have no investigation notes at all, above the "
                f"{round(empty_fraction_threshold * 100, 1)}% threshold — a "
                "potential investigation record-keeping gap."
            ),
            "evidence": {
                "case_count": int(len(group)),
                "empty_investigation_fraction": round(empty_fraction, 3),
                "threshold_fraction": empty_fraction_threshold,
            },
        })
    return pd.DataFrame(findings)


def peer_group_map(data: dict, cfg: dict) -> dict:
    """
    soc_id -> peer group, using the same rule as peer benchmarking.

    Returns an empty mapping when the submission carries no entity
    table or no sector column, which callers read as "no peer
    information available" rather than as "every entity is a peer".
    """
    socs = (data or {}).get("socs")
    if socs is None or socs.empty or "soc_id" not in socs.columns:
        return {}
    if cfg.get("benchmarking", {}).get("peer_group_by") == "sector" \
            and "sector" not in socs.columns:
        return {}

    grouped = add_peer_group(socs, cfg)
    return dict(zip(grouped["soc_id"], grouped["peer_group"]))


def run_all_negative_space_detectors(data: dict, alert_volume_by_soc: pd.DataFrame,
                                       alert_categories_by_soc: pd.DataFrame, cfg: dict,
                                       alerts_enriched: pd.DataFrame = None) -> pd.DataFrame:
    peers = peer_group_map(data, cfg)
    frames = [
        detect_telemetry_gaps(data["telemetry"], cfg),
        detect_missing_categories(alert_categories_by_soc, cfg, peers),
        detect_low_activity_outliers(alert_volume_by_soc, cfg, peers),
        detect_missing_investigations(data.get("cases", pd.DataFrame()), cfg),
    ]
    if alerts_enriched is not None:
        frames.append(detect_missing_escalation_records(alerts_enriched, cfg))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
