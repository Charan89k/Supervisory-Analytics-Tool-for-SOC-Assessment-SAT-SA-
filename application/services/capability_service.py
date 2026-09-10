"""
Supervisory capability areas.

The official problem statement frames the whole exercise as assessing
eight capabilities — threat detection, investigation, escalation,
incident response, security operations, governance and oversight,
operational discipline, cyber resilience. Findings are the evidence;
capabilities are what a supervisor is actually asked to form a view on.

This module is a lookup and an aggregation, nothing more. The mapping
lives in `capability_mapping` in the assessment configuration, so a
supervisor can inspect it, disagree with an assignment on its merits,
and change it without touching code. No model participates in deciding
which capability a finding speaks to.

Every rule has exactly ONE primary capability. A finding usually informs
several, but attributing it to several would double-count it in every
per-capability total and make the arithmetic uncheckable. Secondary
areas travel with the finding for context and are never scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

UNMAPPED = "UNMAPPED"


@dataclass(frozen=True)
class CapabilityArea:
    key: str
    label: str
    question: str


@dataclass
class RuleMapping:
    finding_type: str
    primary: str
    secondary: List[str] = field(default_factory=list)
    why: str = ""


@dataclass
class CapabilityResult:
    """One capability area's standing for one entity."""

    area: CapabilityArea
    finding_count: int = 0
    contribution: float = 0.0
    rules_fired: Dict[str, int] = field(default_factory=dict)
    rules_available: List[str] = field(default_factory=list)

    @property
    def assessed(self) -> bool:
        """
        Whether any rule covering this capability could have fired.

        Distinct from having no findings: a capability with no rules
        mapped to it is not being assessed at all, and reporting that as
        "no concerns" would overstate what the tool examined.
        """
        return bool(self.rules_available)

    @property
    def status(self) -> str:
        if not self.assessed:
            return "not assessed"
        if self.finding_count == 0:
            return "no findings"
        return "findings recorded"

    def summary(self) -> str:
        if not self.assessed:
            return (f"No rule in the current rule set assesses "
                    f"{self.area.label.lower()}.")
        if self.finding_count == 0:
            return (f"No findings from the "
                    f"{len(self.rules_available)} rule(s) covering this area.")

        top = sorted(self.rules_fired.items(), key=lambda kv: -kv[1])
        named = ", ".join(
            f"{name.replace('_', ' ').lower()} ({count})"
            for name, count in top[:3])
        return f"{self.finding_count} finding(s): {named}."


def load_areas(config: dict) -> Dict[str, CapabilityArea]:
    mapping = (config or {}).get("capability_mapping", {}) or {}
    return {
        key: CapabilityArea(key=key,
                             label=str(meta.get("label", key)),
                             question=str(meta.get("question", "")))
        for key, meta in (mapping.get("areas", {}) or {}).items()
    }


def load_rule_mappings(config: dict) -> Dict[str, RuleMapping]:
    mapping = (config or {}).get("capability_mapping", {}) or {}
    return {
        finding_type: RuleMapping(
            finding_type=finding_type,
            primary=str(entry.get("primary", UNMAPPED)),
            secondary=list(entry.get("secondary", []) or []),
            why=str(entry.get("why", "")).strip(),
        )
        for finding_type, entry in (mapping.get("rules", {}) or {}).items()
    }


def capability_for(finding_type: str, config: dict) -> Optional[RuleMapping]:
    """
    The capability mapping for one finding type, or None if unmapped.

    Returns None rather than guessing. A rule added without a mapping
    should be visibly unmapped, not silently filed under whichever area
    seems closest.
    """
    return load_rule_mappings(config).get(str(finding_type))


def capability_label(finding_type: str, config: dict) -> str:
    mapping = capability_for(finding_type, config)
    if mapping is None:
        return ""
    areas = load_areas(config)
    area = areas.get(mapping.primary)
    return area.label if area else mapping.primary


def assess_entity(entity: dict, config: dict) -> List[CapabilityResult]:
    """
    Every capability area's standing for one entity, in configured order.

    Contribution is summed from `score_breakdown`, which the engine
    already publishes, so a capability's share of the risk score is the
    same arithmetic a supervisor can redo by hand from the finding
    counts and the configured weights.
    """
    areas = load_areas(config)
    mappings = load_rule_mappings(config)

    results = {
        key: CapabilityResult(area=area) for key, area in areas.items()
    }

    # Which rules cover each area at all, whether or not they fired.
    for finding_type, mapping in mappings.items():
        result = results.get(mapping.primary)
        if result is not None:
            result.rules_available.append(finding_type)

    for component in (entity or {}).get("score_breakdown", []):
        finding_type = str(component.get("finding_type", "")).upper()
        mapping = mappings.get(finding_type)
        if mapping is None:
            continue

        result = results.get(mapping.primary)
        if result is None:
            continue

        count = int(component.get("raw_count", 0))
        if count:
            result.finding_count += count
            result.rules_fired[finding_type] = count
        result.contribution += float(component.get("contribution", 0.0))

    return list(results.values())


def coverage(config: dict) -> dict:
    """
    How much of the capability framework the rule set actually covers.

    Reported plainly. A tool that assesses six of eight areas and
    presents itself as assessing all eight is overstating its own
    reach, and a supervisor relying on it would not know which
    questions remain unanswered.
    """
    areas = load_areas(config)
    mappings = load_rule_mappings(config)

    covered: Dict[str, List[str]] = {key: [] for key in areas}
    unmapped_rules = []

    for finding_type, mapping in mappings.items():
        if mapping.primary in covered:
            covered[mapping.primary].append(finding_type)
        else:
            unmapped_rules.append(finding_type)

    assessed = [key for key, rules in covered.items() if rules]
    unassessed = [key for key, rules in covered.items() if not rules]

    return {
        "areas": len(areas),
        "assessed": assessed,
        "unassessed": unassessed,
        "rules_by_area": covered,
        "unmapped_rules": unmapped_rules,
    }


def coverage_statement(config: dict) -> str:
    report = coverage(config)
    areas = load_areas(config)

    if report["unassessed"]:
        names = ", ".join(areas[key].label for key in report["unassessed"])
        return (
            f"The current rule set assesses {len(report['assessed'])} of "
            f"{report['areas']} supervisory capability areas. Not assessed: "
            f"{names}. Findings cannot speak to a capability no rule covers."
        )

    return (
        f"All {report['areas']} supervisory capability areas are covered by "
        f"at least one detection rule. Capability standing is derived from "
        f"findings, not asserted: an area with no findings means no rule "
        f"fired, not that the capability was independently verified."
    )
