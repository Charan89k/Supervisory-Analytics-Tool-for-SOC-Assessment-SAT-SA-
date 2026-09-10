"""
Trend analysis across submission periods.

The official requirement is trend analysis across entities and time
periods. Two design decisions shape how it is done here.

**Each period is assessed independently, then compared.** The obvious
alternative — concatenating periods into one dataset (which
`load_multiple_periods` does) and assessing once — corrupts every
per-period statistic. Risk scores normalise per 100 alerts, so a period
with more alerts distorts the whole score; LOW_ACTIVITY_OUTLIER and
ANALYST_OVERLOAD are z-scores that become meaningless across mixed
periods; and the detectors select fixed columns, so a period tag would
be dropped before findings are emitted anyway. Assessing per period
keeps each number correct and makes the comparison a comparison of
like with like.

**Nothing is extrapolated, smoothed, or projected.** A trend here is
the observed sequence of values and the arithmetic difference between
its ends. No line is fitted, no direction is predicted, and a single
period yields no trend at all — see `TrendReport.available`. Supervisory
findings must be defensible to the entity being assessed, and a fitted
slope over three points is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: A change smaller than this is reported as "stable" rather than as a
#: direction. Scores wobble on sampling alone, and calling that a trend
#: would manufacture movement the data does not support.
STABLE_BAND = 0.05

#: How consistently a series must move in one direction before it counts
#: as a trend, measured as |net change| / sum of the absolute
#: period-to-period steps.
#:
#: A net difference between two endpoints is not a trend. On the seeded
#: dataset the two entities with deliberate trajectories score 1.00 —
#: every step in the same direction — while the two seeded as UNCHANGED
#: score 0.56 and 0.11 despite net changes of -6.4 and -7.3 points.
#: Judging on net change alone reported both of those as falling, which
#: is sampling noise presented to a supervisor as a finding.
#:
#: A monotonic series scores 1.0; a random walk scores near 0. 0.7
#: admits a trend with one contrary step among several.
TREND_CONSISTENCY = 0.7


@dataclass
class Series:
    """One measure tracked across periods, for one entity or overall."""

    key: str
    label: str
    periods: List[str] = field(default_factory=list)
    values: List[float] = field(default_factory=list)

    @property
    def first(self) -> Optional[float]:
        return self.values[0] if self.values else None

    @property
    def last(self) -> Optional[float]:
        return self.values[-1] if self.values else None

    @property
    def change(self) -> Optional[float]:
        if len(self.values) < 2:
            return None
        return self.values[-1] - self.values[0]

    @property
    def steps(self) -> List[float]:
        return [self.values[i + 1] - self.values[i]
                for i in range(len(self.values) - 1)]

    @property
    def consistency(self) -> Optional[float]:
        """
        How much of the total movement was in one direction: 1.0 for a
        monotonic series, near 0 for a random walk.
        """
        steps = self.steps
        if not steps:
            return None
        travelled = sum(abs(step) for step in steps)
        if travelled == 0:
            return 0.0
        return abs(sum(steps)) / travelled

    @property
    def direction(self) -> str:
        """
        "up", "down", "mixed" or "stable".

        Deliberately not "improving" or "worsening": whether a rise is
        bad depends on the measure, and that judgement belongs to the
        supervisor reading it.

        "mixed" is the honest answer for a series that moved but not
        consistently — the endpoints differ, the path wandered, and no
        direction is supportable.
        """
        change = self.change
        if change is None:
            return "unknown"
        if abs(change) < STABLE_BAND:
            return "stable"

        consistency = self.consistency
        if consistency is not None and consistency < TREND_CONSISTENCY:
            return "mixed"

        return "up" if change > 0 else "down"

    @property
    def is_trend(self) -> bool:
        return self.direction in ("up", "down")

    def change_label(self, unit: str = "") -> str:
        change = self.change
        if change is None:
            return "no comparison (single period)"
        if self.direction == "stable":
            return "stable across all periods"
        if self.direction == "mixed":
            return (f"varied between {min(self.values):,.1f} and "
                    f"{max(self.values):,.1f}{unit} with no consistent "
                    f"direction")
        sign = "+" if change > 0 else ""
        return f"{sign}{change:,.2f}{unit} over {len(self.values)} periods"


@dataclass
class RankChange:
    """How an entity's position in the risk ranking moved."""

    soc_id: str
    first_rank: int
    last_rank: int

    @property
    def change(self) -> int:
        """Positive means it moved UP the ranking (toward rank 1)."""
        return self.first_rank - self.last_rank

    @property
    def label(self) -> str:
        if self.change == 0:
            return f"unchanged at #{self.last_rank}"
        direction = "worse" if self.change > 0 else "better"
        return (f"#{self.first_rank} -> #{self.last_rank} "
                f"({abs(self.change)} place(s) {direction})")


@dataclass
class RepeatedFinding:
    """A rule that fired for the same entity in more than one period."""

    soc_id: str
    finding_type: str
    periods_seen: List[str] = field(default_factory=list)
    counts: List[int] = field(default_factory=list)

    @property
    def period_count(self) -> int:
        return len(self.periods_seen)

    @property
    def persistent(self) -> bool:
        """Present in every period assessed — never resolved."""
        return False  # set by build_trends, which knows the period total


@dataclass
class TrendReport:
    periods: List[str] = field(default_factory=list)
    available: bool = False
    message: str = ""

    #: soc_id -> Series, one per tracked measure
    risk_score: Dict[str, Series] = field(default_factory=dict)
    total_findings: Dict[str, Series] = field(default_factory=dict)
    execution_gaps: Dict[str, Series] = field(default_factory=dict)
    negative_space: Dict[str, Series] = field(default_factory=dict)

    rank_changes: List[RankChange] = field(default_factory=list)
    repeated_findings: List[RepeatedFinding] = field(default_factory=list)
    #: Overall totals across all entities, per period.
    portfolio: Dict[str, Series] = field(default_factory=dict)

    def entities(self) -> List[str]:
        return sorted(self.risk_score)

    def summary(self) -> str:
        if not self.available:
            return self.message
        moved = [r for r in self.rank_changes if r.change != 0]
        trending = [s for s in self.risk_score.values() if s.is_trend]
        return (
            f"{len(self.periods)} submission periods "
            f"({self.periods[0]} to {self.periods[-1]}). "
            f"{len(trending)} of {len(self.risk_score)} entities show a "
            f"consistent risk-score trend; {len(moved)} changed position "
            f"in the risk ranking."
        )


def _entity_index(results: dict) -> Dict[str, dict]:
    return {e["soc_id"]: e for e in (results or {}).get("entities", [])}


def build_trends(period_results: Sequence[Tuple[str, dict]]) -> TrendReport:
    """
    Compare a sequence of (period_label, assessment_results).

    Periods must arrive oldest first. Entities absent from a period are
    skipped for that period rather than recorded as zero: an entity that
    did not submit is not an entity with no findings, and conflating the
    two would invent an improvement.
    """
    report = TrendReport()

    if not period_results:
        report.message = "No assessment loaded."
        return report

    report.periods = [label for label, _ in period_results]

    if len(period_results) < 2:
        report.message = (
            "Single assessment period — no trend data. Trends become "
            "available once submissions from more than one period are "
            "assessed together.")
        return report

    report.available = True
    indexed = [(label, _entity_index(results))
               for label, results in period_results]

    all_entities = sorted({soc for _, index in indexed for soc in index})

    measures = (
        ("risk_score", "Supervisory risk score",
         lambda e: float(e.get("supervisory_risk_score", 0.0))),
        ("total_findings", "Total findings",
         lambda e: float(len(e.get("execution_gap_findings", []))
                          + len(e.get("negative_space_findings", [])))),
        ("execution_gaps", "Execution gaps",
         lambda e: float(len(e.get("execution_gap_findings", [])))),
        ("negative_space", "Negative space",
         lambda e: float(len(e.get("negative_space_findings", [])))),
    )

    for key, label, extract in measures:
        target = getattr(report, key)
        for soc_id in all_entities:
            series = Series(key=key, label=label)
            for period_label, index in indexed:
                entity = index.get(soc_id)
                if entity is None:
                    continue   # did not submit; not the same as zero
                series.periods.append(period_label)
                series.values.append(extract(entity))
            target[soc_id] = series

        # Portfolio-wide total per period.
        portfolio = Series(key=key, label=f"{label} (all entities)")
        for period_label, index in indexed:
            portfolio.periods.append(period_label)
            portfolio.values.append(
                sum(extract(entity) for entity in index.values()))
        report.portfolio[key] = portfolio

    # ---- ranking movement -------------------------------------------
    first_index, last_index = indexed[0][1], indexed[-1][1]
    for soc_id in all_entities:
        first, last = first_index.get(soc_id), last_index.get(soc_id)
        if first is None or last is None:
            continue
        report.rank_changes.append(RankChange(
            soc_id=soc_id,
            first_rank=int(first.get("priority_rank", 0)),
            last_rank=int(last.get("priority_rank", 0)),
        ))
    report.rank_changes.sort(key=lambda r: r.last_rank)

    # ---- repeated findings ------------------------------------------
    # A rule firing for one entity across several periods is a different
    # supervisory signal from the same count appearing once: it says the
    # condition was never resolved.
    tracker: Dict[Tuple[str, str], RepeatedFinding] = {}
    for period_label, index in indexed:
        for soc_id, entity in index.items():
            counts: Dict[str, int] = {}
            for finding in (entity.get("execution_gap_findings", [])
                            + entity.get("negative_space_findings", [])):
                key = str(finding.get("finding_type", ""))
                counts[key] = counts.get(key, 0) + 1

            for finding_type, count in counts.items():
                record = tracker.setdefault(
                    (soc_id, finding_type),
                    RepeatedFinding(soc_id=soc_id, finding_type=finding_type))
                record.periods_seen.append(period_label)
                record.counts.append(count)

    report.repeated_findings = sorted(
        (r for r in tracker.values() if r.period_count > 1),
        key=lambda r: (-r.period_count, -sum(r.counts), r.soc_id),
    )

    return report


def persistent_findings(report: TrendReport) -> List[RepeatedFinding]:
    """Findings present in EVERY period — conditions never resolved."""
    total = len(report.periods)
    return [r for r in report.repeated_findings if r.period_count == total]
