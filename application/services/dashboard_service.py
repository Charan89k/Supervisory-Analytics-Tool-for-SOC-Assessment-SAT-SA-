"""
Dashboard computation.

Turns an assessment result into the structures the supervisory dashboard
renders. Kept out of the UI so the numbers a supervisor acts on can be
tested without constructing a window, and so the dashboard cannot
quietly acquire analytics of its own — everything here is a projection
of what the deterministic engine already produced.

The page is organised around four questions:

    WHO needs attention?      -> entity_rankings()
    WHY?                      -> risk_drivers()
    WHAT evidence supports it? -> distributions()
    WHAT should be reviewed?   -> review_preview()
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

#: Rules whose finding is produced by a statistical outlier test rather
#: than a fixed threshold. Reported separately because "this entity
#: deviates from its peers" is a different kind of claim from "this
#: entity breached a documented SLA", and a supervisor weighs them
#: differently.
ANOMALY_RULES = frozenset({"LOW_ACTIVITY_OUTLIER", "ANALYST_OVERLOAD"})

SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

#: Peers required before a percentile or a deviation from the peer
#: median means anything.
#:
#: On the current synthetic data the sector peer groups hold one and two
#: entities. A "100th percentile" drawn from a group of one is an
#: artefact of the entity being compared with itself, and from a group
#: of two it says only "the worse of the two" — neither is a benchmark.
#: Showing them unqualified would tell a supervisor something the data
#: does not support, so below this threshold the dashboard reports the
#: group size instead of a comparison.
MIN_PEERS_FOR_COMPARISON = 3


def _iter_findings(entity: dict):
    yield from entity.get("execution_gap_findings", [])
    yield from entity.get("negative_space_findings", [])


def _severity_of(finding: dict) -> str:
    value = finding.get("severity")
    if value is None:
        return "UNRATED"
    text = str(value).strip().upper()
    return text if text in SEVERITY_ORDER else "UNRATED"


@dataclass
class Overview:
    """Portfolio-level totals — the scale of what was assessed."""

    entities: int = 0
    total_findings: int = 0
    execution_gaps: int = 0
    negative_space: int = 0
    anomalies: int = 0
    critical: int = 0
    high: int = 0
    review_queue: int = 0
    explained: int = 0
    total_alerts: int = 0


@dataclass
class EntityRow:
    """One row of the risk ranking — the WHO."""

    rank: int
    soc_id: str
    organization: str
    peer_group: str
    risk_score: float
    percentile: float
    peer_median: float
    deviation: float
    execution_gaps: int
    negative_space: int
    critical: int
    high: int
    peer_count: int = 1

    #: Fraction of the records the checks depend on that this entity
    #: actually submitted. None when nothing could be measured.
    evidence_coverage: Optional[float] = None
    #: Rules whose evidence base is materially incomplete here.
    suppressed_rules: List[str] = field(default_factory=list)
    #: The sentence a supervisor must read next to the score.
    evidence_caveat: str = ""

    @property
    def evidence_limited(self) -> bool:
        return bool(self.suppressed_rules)

    @property
    def evidence_label(self) -> str:
        """
        Coverage as a column value. A low figure is not a verdict — it
        says how much of the submission could be examined at all, which
        is what stops a low risk score being read as a clean one.
        """
        if self.evidence_coverage is None:
            return "—"
        return f"{self.evidence_coverage * 100:.0f}%"

    @property
    def comparable(self) -> bool:
        return self.peer_count >= MIN_PEERS_FOR_COMPARISON

    @property
    def percentile_label(self) -> str:
        if not self.comparable:
            return f"n={self.peer_count}"
        return f"{self.percentile:.0f}th"

    @property
    def deviation_label(self) -> str:
        """
        Position relative to the peer median, phrased as a comparison
        rather than a verdict. Being different from peers is an
        indicator worth looking at, not evidence of weakness on its own.

        Suppressed entirely when the peer group is too small to support
        the claim.
        """
        if not self.comparable:
            return f"peer group too small (n={self.peer_count})"
        if abs(self.deviation) < 0.05:
            return "at peer median"
        direction = "above" if self.deviation > 0 else "below"
        return f"{abs(self.deviation):.1f} {direction} peer median"


@dataclass
class DriverRow:
    """One contributing component of an entity's score — the WHY."""

    finding_type: str
    raw_count: int
    normalized_per_100: float
    weight: float
    contribution: float
    share: float


@dataclass
class Distribution:
    """Counts by some key, in a fixed display order."""

    labels: List[str] = field(default_factory=list)
    values: List[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.values)


@dataclass
class TrendStatus:
    """
    Whether trend analysis is possible at all.

    A single assessment period cannot produce a trend, and drawing one
    anyway would be fabrication. The dashboard says so plainly instead.
    """

    available: bool
    periods: int
    message: str


def overview(results: dict, review_queue: Optional[List[dict]] = None) -> Overview:
    entities = results.get("entities", []) if results else []
    queue = review_queue if review_queue is not None else (
        results.get("review_queue", []) if results else [])

    summary = Overview(
        entities=len(entities),
        review_queue=len(queue),
        total_alerts=(results or {}).get("run_metadata", {}).get("total_alerts", 0),
    )

    for entity in entities:
        summary.execution_gaps += len(entity.get("execution_gap_findings", []))
        summary.negative_space += len(entity.get("negative_space_findings", []))

        for finding in _iter_findings(entity):
            severity = _severity_of(finding)
            if severity == "CRITICAL":
                summary.critical += 1
            elif severity == "HIGH":
                summary.high += 1
            if finding.get("finding_type") in ANOMALY_RULES:
                summary.anomalies += 1

    summary.total_findings = summary.execution_gaps + summary.negative_space
    summary.explained = sum(
        1 for record in queue
        if record.get("narration_source") not in (None, "rule"))

    return summary


def entity_rankings(results: dict) -> List[EntityRow]:
    """
    The risk ranking, with each entity's position relative to its own
    peer group. Peer median is computed within peer_group, because
    comparing a small telecom SOC against a large financial one says
    nothing useful.
    """
    entities = (results or {}).get("entities", [])
    if not entities:
        return []

    by_group: Dict[str, List[float]] = {}
    for entity in entities:
        group = str(entity.get("peer_group") or "all")
        by_group.setdefault(group, []).append(
            float(entity.get("supervisory_risk_score", 0.0)))

    medians = {group: statistics.median(scores)
               for group, scores in by_group.items()}
    sizes = {group: len(scores) for group, scores in by_group.items()}

    rows = []
    for entity in entities:
        group = str(entity.get("peer_group") or "all")
        score = float(entity.get("supervisory_risk_score", 0.0))
        median = medians.get(group, score)

        completeness = entity.get("evidence_completeness") or {}

        critical = high = 0
        for finding in _iter_findings(entity):
            severity = _severity_of(finding)
            if severity == "CRITICAL":
                critical += 1
            elif severity == "HIGH":
                high += 1

        rows.append(EntityRow(
            rank=int(entity.get("priority_rank", 0)),
            soc_id=str(entity.get("soc_id", "")),
            organization=str(entity.get("organization_name") or ""),
            peer_group=group,
            risk_score=score,
            percentile=float(
                entity.get("supervisory_risk_score_percentile", 0.0)),
            peer_median=median,
            deviation=score - median,
            execution_gaps=len(entity.get("execution_gap_findings", [])),
            negative_space=len(entity.get("negative_space_findings", [])),
            critical=critical,
            high=high,
            peer_count=sizes.get(group, 1),
            evidence_coverage=completeness.get("overall_coverage"),
            suppressed_rules=list(completeness.get("suppressed_rules") or []),
            evidence_caveat=str(completeness.get("caveat") or ""),
        ))

    return sorted(rows, key=lambda row: row.rank)


def peer_comparison_note(rows: List[EntityRow]) -> str:
    """
    One line telling the supervisor how far to trust the peer columns.

    Peer benchmarking is only as good as the peer group; saying so on
    the page is cheaper than having a supervisor discover it later.
    """
    if not rows:
        return ""

    too_small = [row for row in rows if not row.comparable]
    if not too_small:
        return (f"Peer comparison is within sector groups of "
                f"{min(r.peer_count for r in rows)} or more entities.")

    names = ", ".join(sorted({row.peer_group for row in too_small}))
    return (
        f"{len(too_small)} of {len(rows)} entities sit in a sector peer "
        f"group with fewer than {MIN_PEERS_FOR_COMPARISON} members "
        f"({names}). Percentile and peer-median columns are withheld for "
        f"those entities: a comparison against one or two peers is not a "
        f"benchmark. Their risk scores remain fully valid."
    )


def risk_drivers(results: dict, soc_id: str, limit: int = 8) -> List[DriverRow]:
    """
    What actually drove one entity's score, largest contribution first.

    Reads score_breakdown, which the engine already publishes, so every
    number here can be recomputed by hand from the finding counts and
    the weights in the assessment configuration.
    """
    entity = next(
        (e for e in (results or {}).get("entities", [])
         if e.get("soc_id") == soc_id), None)
    if entity is None:
        return []

    components = [c for c in entity.get("score_breakdown", [])
                  if float(c.get("contribution", 0)) > 0]
    total = sum(float(c.get("contribution", 0)) for c in components) or 1.0

    components.sort(key=lambda c: float(c.get("contribution", 0)), reverse=True)

    return [
        DriverRow(
            finding_type=str(c.get("finding_type", "")),
            raw_count=int(c.get("raw_count", 0)),
            normalized_per_100=float(c.get("normalized_per_100", 0.0)),
            weight=float(c.get("weight", 0.0)),
            contribution=float(c.get("contribution", 0.0)),
            share=float(c.get("contribution", 0.0)) / total,
        )
        for c in components[:limit]
    ]


def severity_distribution(results: dict,
                           soc_id: Optional[str] = None) -> Distribution:
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    unrated = 0

    for entity in (results or {}).get("entities", []):
        if soc_id and entity.get("soc_id") != soc_id:
            continue
        for finding in _iter_findings(entity):
            severity = _severity_of(finding)
            if severity == "UNRATED":
                unrated += 1
            else:
                counts[severity] += 1

    labels = list(SEVERITY_ORDER)
    values = [counts[s] for s in SEVERITY_ORDER]

    # Entity-level rules (telemetry gaps, record-keeping) carry no alert
    # severity. Shown rather than dropped: silently discarding findings
    # from a distribution makes the totals disagree with the overview.
    if unrated:
        labels.append("UNRATED")
        values.append(unrated)

    return Distribution(labels=labels, values=values)


def category_distribution(results: dict,
                           soc_id: Optional[str] = None) -> Distribution:
    execution = negative = 0
    for entity in (results or {}).get("entities", []):
        if soc_id and entity.get("soc_id") != soc_id:
            continue
        execution += len(entity.get("execution_gap_findings", []))
        negative += len(entity.get("negative_space_findings", []))

    return Distribution(labels=["Execution Gaps", "Negative Space"],
                         values=[execution, negative])


def finding_type_distribution(results: dict, soc_id: Optional[str] = None,
                                limit: int = 10) -> Distribution:
    counts: Dict[str, int] = {}
    for entity in (results or {}).get("entities", []):
        if soc_id and entity.get("soc_id") != soc_id:
            continue
        for finding in _iter_findings(entity):
            key = str(finding.get("finding_type", "UNKNOWN"))
            counts[key] = counts.get(key, 0) + 1

    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return Distribution(labels=[k for k, _ in ordered],
                         values=[v for _, v in ordered])


def review_preview(review_queue: List[dict], limit: int = 10) -> List[dict]:
    """Top of the supervisory review queue — the WHAT TO REVIEW."""
    return sorted(
        review_queue or [],
        key=lambda record: record.get("queue_rank", 10 ** 9),
    )[:limit]


def trend_status(results: dict) -> TrendStatus:
    """
    Trends require more than one assessment period. With one period the
    honest answer is that there is nothing to trend — not a flat line
    drawn from a single point.
    """
    entities = (results or {}).get("entities", [])
    if not entities:
        return TrendStatus(False, 0, "No assessment loaded.")

    periods = (results.get("run_metadata", {}) or {}).get(
        "submission_periods", 1)

    if periods and periods > 1:
        return TrendStatus(True, periods,
                            f"{periods} assessment periods available.")

    return TrendStatus(
        False, 1,
        "Single assessment period — no trend data. Trends become "
        "available once submissions from more than one period are "
        "assessed together.")
