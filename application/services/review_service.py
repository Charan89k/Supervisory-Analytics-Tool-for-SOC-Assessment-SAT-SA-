"""
Supervisory review queue — case grouping and prioritisation reasons.

The queue's whole purpose, in review_queue.py's own words, is
"correlating multiple findings on the same alert into a single case so
a supervisor sees compounding problems together instead of as unrelated
rows". The engine does that work and publishes case_rank,
case_priority and case_finding_count on every record.

Rendering the queue as a flat list throws that away. On the current
data, 81 findings correlate into 25 cases, and the worst case is a
single alert that was acknowledged without investigation, closed with
templated notes, left with no evidence record, and triaged late — four
findings that mean far more together than apart. A flat table shows
them as four unrelated rows.

This module reassembles the cases and states, in arithmetic, why each
one ranks where it does. It computes no priority of its own: every
number here is read from what the engine already published, so the
queue a supervisor sees is the queue the engine built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNRATED": 4}


def severity_of(record: dict) -> str:
    value = record.get("severity")
    if value is None:
        return "UNRATED"
    text = str(value).strip().upper()
    return text if text in SEVERITY_RANK else "UNRATED"


@dataclass
class ReviewCase:
    """One correlated case: the findings the engine grouped together."""

    case_rank: int
    soc_id: str
    case_priority: float
    findings: List[dict] = field(default_factory=list)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def alert_id(self) -> Optional[str]:
        for finding in self.findings:
            if finding.get("alert_id"):
                return str(finding["alert_id"])
        return None

    @property
    def case_id(self) -> Optional[str]:
        for finding in self.findings:
            if finding.get("case_id"):
                return str(finding["case_id"])
        return None

    @property
    def worst_severity(self) -> str:
        return min((severity_of(f) for f in self.findings),
                   key=lambda s: SEVERITY_RANK[s], default="UNRATED")

    @property
    def top_queue_rank(self) -> int:
        return min((int(f.get("queue_rank", 10 ** 9)) for f in self.findings),
                   default=10 ** 9)

    @property
    def summary(self) -> str:
        """
        What this case is, in one line. Names the distinct rules that
        fired rather than repeating a finding type that occurred twice.
        """
        types = []
        for finding in self.findings:
            label = str(finding.get("finding_type", "")).replace("_", " ").title()
            if label and label not in types:
                types.append(label)
        return ", ".join(types)

    def why_prioritised(self) -> str:
        """
        The arithmetic behind this case's position, recomputable by hand.

        Nothing is asserted here that the engine did not publish: each
        finding's weight and severity boost multiply to its queue
        priority, and the case priority is their sum.
        """
        lines = []
        for finding in sorted(
            self.findings,
            key=lambda f: float(f.get("queue_priority", 0)), reverse=True
        ):
            weight = float(finding.get("finding_weight", 0))
            boost = float(finding.get("severity_boost", 0))
            priority = float(finding.get("queue_priority", 0))
            lines.append(
                f"  {str(finding.get('finding_type', '')):36} "
                f"weight {weight:g} x severity {boost:g} = {priority:g}")

        total = sum(float(f.get("queue_priority", 0)) for f in self.findings)
        lines.append(f"  {'CASE PRIORITY (sum)':36} {total:g}")

        if self.finding_count > 1:
            lines.append("")
            lines.append(
                f"  These {self.finding_count} findings share one alert. "
                f"They are ranked together because problems compounding on "
                f"the same alert matter more than the same problems spread "
                f"across unrelated ones.")

        return "\n".join(lines)


def build_cases(review_queue: List[dict]) -> List[ReviewCase]:
    """
    Reassemble the engine's cases from the queue records.

    Grouped on case_rank, which the engine assigns per correlated case.
    Records missing a case_rank each become their own case rather than
    being dropped — a queue item with no case must still be reviewable.
    """
    grouped: Dict[object, ReviewCase] = {}

    for index, record in enumerate(review_queue or []):
        rank = record.get("case_rank")
        key = rank if rank is not None else f"_ungrouped_{index}"

        case = grouped.get(key)
        if case is None:
            case = ReviewCase(
                case_rank=int(rank) if rank is not None else 10 ** 9,
                soc_id=str(record.get("soc_id", "")),
                case_priority=float(record.get("case_priority", 0.0)),
            )
            grouped[key] = case

        case.findings.append(record)

    cases = list(grouped.values())
    for case in cases:
        case.findings.sort(
            key=lambda f: float(f.get("queue_priority", 0)), reverse=True)

    cases.sort(key=lambda c: (c.case_rank, c.top_queue_rank))
    return cases


def queue_statistics(cases: List[ReviewCase]) -> dict:
    """Headline numbers for the page, derived only from the cases."""
    findings = sum(case.finding_count for case in cases)
    compound = [case for case in cases if case.finding_count > 1]

    severities: Dict[str, int] = {}
    for case in cases:
        key = case.worst_severity
        severities[key] = severities.get(key, 0) + 1

    return {
        "cases": len(cases),
        "findings": findings,
        "compound_cases": len(compound),
        "largest_case": max((c.finding_count for c in cases), default=0),
        "by_severity": severities,
    }


def filter_cases(cases: List[ReviewCase], soc_id: str = "",
                  severity: str = "", query: str = "",
                  compound_only: bool = False) -> List[ReviewCase]:
    """
    Narrow the case list. Selection only — no case is altered, and the
    engine's ordering is preserved within whatever survives.
    """
    query = (query or "").strip().lower()
    results = []

    for case in cases:
        if soc_id and case.soc_id != soc_id:
            continue
        if severity and case.worst_severity != severity:
            continue
        if compound_only and case.finding_count < 2:
            continue

        if query:
            haystack = " ".join([
                case.soc_id, case.summary, str(case.alert_id),
                str(case.case_id),
                " ".join(str(f.get("rule_id", "")) for f in case.findings),
                " ".join(str(f.get("rationale", "")) for f in case.findings),
            ]).lower()
            if query not in haystack:
                continue

        results.append(case)

    return results
