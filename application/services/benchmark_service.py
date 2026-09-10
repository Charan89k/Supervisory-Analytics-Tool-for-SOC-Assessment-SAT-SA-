"""
Peer benchmarking.

Answers "how does this entity compare with entities like it, and on
what?" — the second half being the part that matters. Knowing an entity
sits above its peer median says nothing a supervisor can act on;
knowing it sits above them specifically on missed escalations, at 3.2
per 100 alerts against a peer median of 0.4, is a place to start.

Comparisons are drawn from `score_breakdown`, which the engine already
publishes per entity: raw count, count normalised per 100 alerts, the
configured weight, and the resulting contribution. Normalising per 100
alerts is what makes the comparison fair — a large entity is not more
concerning merely for handling more alerts — and it means every number
here is one a supervisor can recompute by hand.

Two rules govern the wording throughout:

  * Deviation is not a verdict. An entity differing from its peers is
    an indicator worth examining, not evidence of weakness. A different
    technology footprint, a different threat profile, or a different
    reporting practice all produce deviation without implying anything
    about security.
  * A comparison the data cannot support is not made. Below
    MIN_PEERS a percentile is an artefact of an entity being compared
    with itself or with one other, so the group size is reported
    instead.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

#: Peers required before a percentile or a deviation means anything.
MIN_PEERS = 3

#: A per-100-alert rate difference smaller than this is reported as "in
#: line with peers" rather than as a deviation. Rates wobble between
#: entities on sampling alone.
RATE_NOISE_FLOOR = 0.05


@dataclass
class MetricComparison:
    """One finding type, this entity against its peer group."""

    finding_type: str
    raw_count: int
    entity_rate: float          # per 100 alerts
    peer_median_rate: float
    peer_rates: List[float] = field(default_factory=list)

    @property
    def deviation(self) -> float:
        return self.entity_rate - self.peer_median_rate

    @property
    def direction(self) -> str:
        if abs(self.deviation) < RATE_NOISE_FLOOR:
            return "in line"
        return "above" if self.deviation > 0 else "below"

    @property
    def multiple(self) -> Optional[float]:
        """How many times the peer median, when that is meaningful."""
        if self.peer_median_rate <= 0:
            return None
        return self.entity_rate / self.peer_median_rate

    def label(self) -> str:
        if self.direction == "in line":
            return "in line with peers"

        multiple = self.multiple
        if multiple is not None and multiple >= 2:
            return (f"{multiple:.1f}x the peer median "
                    f"({self.entity_rate:.2f} vs {self.peer_median_rate:.2f} "
                    f"per 100 alerts)")

        return (f"{abs(self.deviation):.2f} per 100 alerts {self.direction} "
                f"the peer median ({self.entity_rate:.2f} vs "
                f"{self.peer_median_rate:.2f})")


@dataclass
class PeerPosition:
    """Where one entity sits within its peer group."""

    soc_id: str
    organization: str
    peer_group: str
    peer_count: int
    risk_score: float
    peer_median: float
    peer_min: float
    peer_max: float
    percentile: float
    rank_in_group: int
    comparisons: List[MetricComparison] = field(default_factory=list)

    @property
    def comparable(self) -> bool:
        return self.peer_count >= MIN_PEERS

    @property
    def deviation(self) -> float:
        return self.risk_score - self.peer_median

    @property
    def position_label(self) -> str:
        if not self.comparable:
            return (f"peer group has only {self.peer_count} member(s) — "
                    f"too small to benchmark against")
        if abs(self.deviation) < 0.05:
            return f"at the peer median for {self.peer_group}"
        direction = "above" if self.deviation > 0 else "below"
        return (f"{abs(self.deviation):,.1f} points {direction} the "
                f"{self.peer_group} peer median, ranked {self.rank_in_group} "
                f"of {self.peer_count}")

    def notable(self, limit: int = 6) -> List[MetricComparison]:
        """
        The finding types that most separate this entity from its peers,
        largest absolute deviation first. These are what a supervisor
        would examine to understand the difference.
        """
        deviating = [c for c in self.comparisons if c.direction != "in line"]
        deviating.sort(key=lambda c: abs(c.deviation), reverse=True)
        return deviating[:limit]


@dataclass
class PeerGroup:
    name: str
    members: List[str] = field(default_factory=list)
    scores: List[float] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def comparable(self) -> bool:
        return self.size >= MIN_PEERS

    @property
    def median(self) -> float:
        return statistics.median(self.scores) if self.scores else 0.0

    @property
    def spread(self) -> float:
        return (max(self.scores) - min(self.scores)) if self.scores else 0.0

    def summary(self) -> str:
        if not self.comparable:
            return (f"{self.name}: {self.size} entity/entities — below the "
                    f"{MIN_PEERS} needed to benchmark")
        return (f"{self.name}: {self.size} entities, median "
                f"{self.median:,.1f}, spread {self.spread:,.1f}")


def _rates(entity: dict) -> Dict[str, float]:
    """finding_type -> count per 100 alerts, from the score breakdown."""
    return {
        str(component.get("finding_type", "")):
            float(component.get("normalized_per_100", 0.0))
        for component in entity.get("score_breakdown", [])
    }


def _counts(entity: dict) -> Dict[str, int]:
    return {
        str(component.get("finding_type", "")):
            int(component.get("raw_count", 0))
        for component in entity.get("score_breakdown", [])
    }


def build_peer_groups(results: dict) -> Dict[str, PeerGroup]:
    groups: Dict[str, PeerGroup] = {}
    for entity in (results or {}).get("entities", []):
        name = str(entity.get("peer_group") or "all")
        group = groups.setdefault(name, PeerGroup(name=name))
        group.members.append(str(entity.get("soc_id", "")))
        group.scores.append(float(entity.get("supervisory_risk_score", 0.0)))
    return groups


def build_positions(results: dict) -> List[PeerPosition]:
    """Where every entity sits relative to its own peer group."""
    entities = (results or {}).get("entities", [])
    if not entities:
        return []

    by_group: Dict[str, List[dict]] = {}
    for entity in entities:
        by_group.setdefault(
            str(entity.get("peer_group") or "all"), []).append(entity)

    positions = []
    for group_name, members in by_group.items():
        scores = [float(e.get("supervisory_risk_score", 0.0)) for e in members]
        median = statistics.median(scores)

        # Peer rates per finding type, computed once for the group.
        peer_rates: Dict[str, List[float]] = {}
        for member in members:
            for finding_type, rate in _rates(member).items():
                peer_rates.setdefault(finding_type, []).append(rate)

        ordered = sorted(
            members,
            key=lambda e: float(e.get("supervisory_risk_score", 0.0)),
            reverse=True)

        for entity in members:
            soc_id = str(entity.get("soc_id", ""))
            score = float(entity.get("supervisory_risk_score", 0.0))
            rates = _rates(entity)
            counts = _counts(entity)

            comparisons = []
            for finding_type, rate in rates.items():
                others = [r for member, r in zip(members,
                                                  peer_rates[finding_type])
                          if str(member.get("soc_id")) != soc_id]
                if not others:
                    continue
                comparisons.append(MetricComparison(
                    finding_type=finding_type,
                    raw_count=counts.get(finding_type, 0),
                    entity_rate=rate,
                    peer_median_rate=statistics.median(others),
                    peer_rates=others,
                ))

            # Percentile within the group: the share of peers this
            # entity scores at or above.
            at_or_below = sum(1 for s in scores if s <= score)
            percentile = (at_or_below / len(scores)) * 100 if scores else 0.0

            positions.append(PeerPosition(
                soc_id=soc_id,
                organization=str(entity.get("organization_name") or ""),
                peer_group=group_name,
                peer_count=len(members),
                risk_score=score,
                peer_median=median,
                peer_min=min(scores),
                peer_max=max(scores),
                percentile=percentile,
                rank_in_group=ordered.index(entity) + 1,
                comparisons=comparisons,
            ))

    positions.sort(key=lambda p: (-p.risk_score, p.soc_id))
    return positions


def benchmarking_caveat(groups: Dict[str, PeerGroup]) -> str:
    """
    How far the peer columns can be trusted, stated on the page.

    Peer benchmarking is only as good as the peer group, and saying so
    costs less than a supervisor discovering it later.
    """
    if not groups:
        return ""

    too_small = [g for g in groups.values() if not g.comparable]
    if not too_small:
        return (
            f"All {len(groups)} peer group(s) hold at least {MIN_PEERS} "
            "entities. Deviation from peers is an indicator for review, "
            "not a finding: entities differ in technology footprint, "
            "threat profile and reporting practice for reasons unrelated "
            "to how well they are run."
        )

    names = ", ".join(sorted(g.name for g in too_small))
    return (
        f"{len(too_small)} of {len(groups)} peer groups hold fewer than "
        f"{MIN_PEERS} entities ({names}). Percentile and peer-median "
        f"figures are withheld for those: a comparison against one or two "
        f"peers is not a benchmark. Their risk scores remain fully valid."
    )
