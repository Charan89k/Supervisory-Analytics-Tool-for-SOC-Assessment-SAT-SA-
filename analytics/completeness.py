"""
Evidence completeness — how much of a submission could actually be checked.

THIS IS NOT A RISK SCORE AND MUST NEVER BE FOLDED INTO ONE. It is
reported alongside the risk score and changes no finding, no severity,
no weight and no ranking.

It exists because the risk score has a blind spot that is structural
rather than accidental. Most execution-gap rules read a record and ask
whether it shows the right thing happened. If the record was never
written, the rule cannot fire — so an entity that keeps poor records
accumulates fewer findings than one keeping good records over identical
behaviour, and therefore scores BETTER.

Measured on the reference dataset, an entity seeded with poor
record-keeping scored 50.6 against 56.4 for a "typical" entity whose
seeded rates are better on every single dimension. Only 32 of its 202
HIGH/CRITICAL alerts carried any escalation record, so 170 alerts were
invisible to MISSED_ESCALATION; only 18% of its cases had investigation
notes, so REPETITIVE_INVESTIGATION had almost nothing to compare. The
negative-space rules exist to counter exactly this, but they fire once
per entity while the rules they compensate for fire once per alert:
0.56 points of compensation against 17.63 points of suppression.

Fixing that by reweighting would mean tuning the risk model until the
symptom disappeared, and would silently change every entity's rank. The
honest alternative is to publish the denominator: state plainly how much
of the submission each check could actually run against, so a supervisor
reading a low score can tell "little went wrong" apart from "little
could be examined".

A low completeness figure is NOT itself a finding of wrongdoing. It may
reflect an export that omitted a table, a case-management system that
stores escalations elsewhere, or genuinely poor record-keeping — and
distinguishing those is supervisory judgement, not arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

#: Below this fraction, a measure is reported as materially limiting
#: what the assessment could examine.
DEFAULT_MIN_COVERAGE = 0.5


@dataclass(frozen=True)
class Coverage:
    """One record type, and how much of it the submission contains."""

    key: str
    label: str
    present: int
    total: int
    #: Rules that cannot fire on a record where this is absent.
    gates: tuple
    #: What the absence means, in a supervisor's terms.
    note: str

    @property
    def fraction(self) -> Optional[float]:
        """None when there is nothing to measure against."""
        if not self.total:
            return None
        return self.present / self.total

    @property
    def missing(self) -> int:
        return max(0, self.total - self.present)

    def limited(self, threshold: float = DEFAULT_MIN_COVERAGE) -> bool:
        fraction = self.fraction
        return fraction is not None and fraction < threshold

    def summary(self) -> str:
        fraction = self.fraction
        if fraction is None:
            return f"{self.label}: nothing to measure."
        return (f"{self.label}: {self.present:,} of {self.total:,} "
                f"({fraction * 100:.1f}%)")


@dataclass
class EntityCompleteness:
    """What could and could not be examined for one entity."""

    soc_id: str
    coverages: List[Coverage] = field(default_factory=list)
    threshold: float = DEFAULT_MIN_COVERAGE

    @property
    def measured(self) -> List[Coverage]:
        return [c for c in self.coverages if c.fraction is not None]

    @property
    def overall(self) -> Optional[float]:
        """
        Unweighted mean of the measures that could be taken.

        Deliberately not weighted by the risk weights of the rules each
        one gates: that would make this a function of the scoring model
        and invite it to be read as a score of its own.
        """
        measured = self.measured
        if not measured:
            return None
        return sum(c.fraction for c in measured) / len(measured)

    @property
    def limited(self) -> List[Coverage]:
        return [c for c in self.measured if c.limited(self.threshold)]

    @property
    def suppressed_rules(self) -> List[str]:
        """Rules whose evidence base is materially incomplete."""
        rules = []
        for coverage in self.limited:
            for rule in coverage.gates:
                if rule not in rules:
                    rules.append(rule)
        return sorted(rules)

    def caveat(self) -> str:
        """
        The sentence a supervisor must read next to the risk score.

        Silence here would be the dangerous case: a clean-looking score
        on a submission that barely contained anything to check.
        """
        if not self.limited:
            return ""

        gaps = "; ".join(c.summary() for c in self.limited)
        rules = ", ".join(self.suppressed_rules)
        return (
            f"Evidence completeness is limited for this entity — {gaps}. "
            f"Checks that read those records ({rules}) could only run on "
            f"the portion that exists, so this entity's finding counts "
            f"and risk score are a floor, not a measurement of how much "
            f"went wrong. Absent records may reflect an incomplete "
            f"export or a system that stores them elsewhere; that is a "
            f"question for the examiner, not a finding on its own."
        )

    def to_dict(self) -> dict:
        return {
            "soc_id": self.soc_id,
            "overall_coverage": (round(self.overall, 3)
                                 if self.overall is not None else None),
            "threshold": self.threshold,
            "limited": bool(self.limited),
            "suppressed_rules": self.suppressed_rules,
            "caveat": self.caveat(),
            "coverages": [
                {
                    "key": c.key,
                    "label": c.label,
                    "present": c.present,
                    "total": c.total,
                    "coverage": (round(c.fraction, 3)
                                 if c.fraction is not None else None),
                    "gates": list(c.gates),
                    "note": c.note,
                }
                for c in self.coverages
            ],
        }


def _entity_coverages(alerts: pd.DataFrame, cases: pd.DataFrame,
                       with_events: int) -> List[Coverage]:
    """
    The record types the execution-gap rules depend on.

    `with_events` is how many of this entity's alerts carry a lifecycle
    event, counted ONCE for the whole submission by the caller. Doing
    it here cost 17s on a 30-entity run: pandas 3 backs strings with
    Arrow, and `isin` against a Python set falls onto a slow path that
    was being walked for every entity over a 218,000-row event table.
    """
    high_crit = alerts[alerts["severity"].isin(["HIGH", "CRITICAL"])] \
        if "severity" in alerts.columns else alerts.iloc[0:0]

    escalation_present = (int(high_crit["escalation_required"].notna().sum())
                          if "escalation_required" in high_crit.columns else 0)

    if cases is not None and not cases.empty and "investigation_notes" in cases.columns:
        notes = cases["investigation_notes"].fillna("").astype(str).str.strip()
        notes_present, notes_total = int((notes != "").sum()), int(len(cases))
    else:
        notes_present, notes_total = 0, int(len(cases)) if cases is not None else 0



    linked = (int(alerts["case_id"].notna().sum())
              if "case_id" in alerts.columns else 0)

    return [
        Coverage(
            key="escalation_records",
            label="HIGH/CRITICAL alerts carrying an escalation record",
            present=escalation_present, total=int(len(high_crit)),
            gates=("MISSED_ESCALATION",),
            note=("Where no escalation record exists, whether escalation "
                  "was required or occurred cannot be determined either "
                  "way."),
        ),
        Coverage(
            key="investigation_notes",
            label="cases carrying investigation notes",
            present=notes_present, total=notes_total,
            gates=("REPETITIVE_INVESTIGATION",),
            note=("Note text is what distinguishes case-specific "
                  "investigation from template-driven work. Absent "
                  "notes cannot be compared."),
        ),
        Coverage(
            key="alert_lifecycle_events",
            label="alerts with a recorded lifecycle event",
            present=with_events, total=int(len(alerts)),
            gates=("ACK_WITHOUT_INVESTIGATION", "SLOW_TRIAGE",
                   "FAST_CLOSURE"),
            note=("Acknowledgement, investigation and closure timings "
                  "are read from lifecycle events."),
        ),
        Coverage(
            key="case_linkage",
            label="alerts linked to a case",
            present=linked, total=int(len(alerts)),
            gates=("REPETITIVE_INVESTIGATION",
                   "REPEATED_ALERT_WITHOUT_REMEDIATION"),
            note="Case-level checks can only run on alerts that have one.",
        ),
    ]


def assess_completeness(alerts_enriched: pd.DataFrame,
                         cases: pd.DataFrame,
                         events: pd.DataFrame,
                         threshold: float = DEFAULT_MIN_COVERAGE
                         ) -> Dict[str, EntityCompleteness]:
    """
    Per-entity evidence completeness for a whole submission.

    Reads the same normalised tables the detectors read, and writes
    nothing back to them.
    """
    if alerts_enriched is None or alerts_enriched.empty:
        return {}

    cases = cases if cases is not None else pd.DataFrame()
    events = events if events is not None else pd.DataFrame()

    # One vectorised pass for the whole submission. `.unique()` keeps
    # this on Arrow's own isin rather than the set-membership path,
    # which is roughly 30x faster here.
    if not events.empty and "alert_id" in events.columns:
        has_event = alerts_enriched["alert_id"].isin(events["alert_id"].unique())
        events_by_soc = has_event.groupby(alerts_enriched["soc_id"]).sum()
    else:
        events_by_soc = {}

    # One grouped pass over cases as well, for the same reason.
    cases_by_soc = (dict(tuple(cases.groupby("soc_id")))
                    if not cases.empty and "soc_id" in cases.columns else {})

    results: Dict[str, EntityCompleteness] = {}
    for soc_id, alerts in alerts_enriched.groupby("soc_id"):
        entity_cases = cases_by_soc.get(soc_id, pd.DataFrame())
        results[str(soc_id)] = EntityCompleteness(
            soc_id=str(soc_id),
            coverages=_entity_coverages(
                alerts, entity_cases,
                int(events_by_soc.get(soc_id, 0)) if len(events_by_soc) else 0),
            threshold=threshold,
        )
    return results
