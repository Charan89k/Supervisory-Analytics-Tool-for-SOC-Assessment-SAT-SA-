"""
Output schema for SAT-SA assessment results.

These dataclasses define the shape of everything the dashboard/report
layer consumes. Keeping this separate from the analytics code means
the frontend team can build against a stable contract while the
detection logic keeps evolving.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class Finding:
    finding_type: str          # e.g. "MISSED_ESCALATION", "TELEMETRY_GAP"
    rule_id: str                # traceable rule reference
    soc_id: str
    rationale: str              # plain-language explanation
    alert_id: Optional[str] = None
    case_id: Optional[str] = None
    severity: Optional[str] = None
    category: str = "EXECUTION_GAP"  # or "NEGATIVE_SPACE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreComponent:
    finding_type: str
    raw_count: int
    normalized_per_100: float
    weight: float
    contribution: float


@dataclass
class EntityAssessment:
    soc_id: str
    organization_name: str
    supervisory_risk_score: float
    priority_rank: int
    peer_group: str
    execution_gap_count: int
    negative_space_count: int
    score_breakdown: List[ScoreComponent] = field(default_factory=list)
    top_findings: List[Finding] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class AssessmentResult:
    """Top-level container for one full assessment run."""
    assessment_period_start: str
    assessment_period_end: str
    entities_assessed: int
    total_findings: int
    entities: List[EntityAssessment] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
