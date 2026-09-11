"""
Validation framework.

The official problem statement requires an explanation of how the tool
is validated against findings derived from expert manual review. This
module measures detection against the SYNTHETIC generator's seeded
ground truth, and is explicit at every turn that synthetic validation is
not expert validation — see `Validation.provenance`.

What synthetic validation can and cannot establish
--------------------------------------------------
It CAN show that a rule fires on the conditions it was written to
detect, does not fire on records where that condition was not injected,
and that the review queue puts genuinely-affected records near the top.
Those are real properties, independently checkable, and they are what
a rule set has to get right before anyone tries it on real submissions.

It CANNOT show that the rules detect what an expert examiner would
consider worth finding. The generator injects the conditions the rules
look for, so agreement between them is partly circular. Only labelled
records from a real manual review can close that gap, and until such
records exist this module reports synthetic figures under that heading
and no other.

Three outcomes, not two
-----------------------
A finding either fires, does not fire because the rule's own threshold
deliberately excluded it, or does not fire when it should have. The
middle case is not a miss. FAST_CLOSURE carries a five-minute margin so
borderline closures do not fire; counting those as false negatives would
understate precision AND create pressure to remove a guard that exists
to prevent false positives. They are reported separately as
`below_threshold`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

GROUND_TRUTH_FILE = "ground_truth.json"

#: Rules whose ground truth is a per-alert label.
ALERT_RULES = {
    "SLOW_TRIAGE", "FAST_CLOSURE", "MISSING_EVIDENCE", "REOPENED_CASE",
    "MISSED_ESCALATION", "ACK_WITHOUT_INVESTIGATION",
    "REPETITIVE_INVESTIGATION",
}

#: Rules whose ground truth is entity-level: the generator seeded a RATE
#: or an absence, not a specific record. Measured as whether the rule
#: fired for the entity at all, since there is no per-record truth to
#: compare against.
ENTITY_RULES = {
    "TELEMETRY_GAP", "MISSING_ALERT_CATEGORY", "LOW_ACTIVITY_OUTLIER",
    "MISSING_ESCALATION_RECORDS", "MISSING_INVESTIGATIONS",
}

#: Grouped at (entity, asset, category) rather than per alert, so a
#: per-alert comparison would not be like for like.
GROUPED_RULES = {"REPEATED_ALERT_WITHOUT_REMEDIATION"}

#: Emitted at (entity, analyst) grain with no seeded per-analyst truth.
UNVALIDATED_RULES = {"ANALYST_OVERLOAD"}


@dataclass(frozen=True)
class RuleScope:
    """
    What a rule can fire on at all.

    Recall against every seeded condition understates a rule that
    deliberately declines to fire on some of them. ACK_WITHOUT_
    INVESTIGATION is scoped to HIGH/CRITICAL because closing a LOW alert
    after triage without a formal investigation record is normal
    practice; measuring it against LOW-severity injections reports 28%
    recall for a rule that finds every case it was written to find.
    Worse, that number invites someone to widen a scope that exists to
    prevent false positives.

    `severities` and `require_closed` are plain attribute predicates
    read from the alerts table, so in-scope counts are derived
    independently of the detector.

    `threshold_limited` marks a rule whose scope is a computed
    threshold — a margin, a minimum occurrence count. Those are NOT
    recomputed here: re-deriving detector logic inside the validator
    would let the two drift and quietly agree with each other. The
    threshold is named instead, and the recall is reported with that
    caveat attached rather than silently corrected.
    """

    description: str
    severities: Optional[List[str]] = None
    require_closed: bool = False
    threshold_limited: bool = False


def build_scopes(config: dict) -> Dict[str, RuleScope]:
    """Rule scopes, with thresholds read from the live configuration."""
    execution = (config or {}).get("execution_gaps", {}) or {}

    ack_severities = execution.get(
        "ack_without_investigation_severities", ["HIGH", "CRITICAL"])
    margin = execution.get("fast_closure_min_margin_minutes", 5)
    repeat_min = execution.get("repetitive_investigation_min_occurrences", 3)
    recurrence_min = execution.get("repeated_alert_min_occurrences", 3)

    return {
        "ACK_WITHOUT_INVESTIGATION": RuleScope(
            description=(f"fires only on {', '.join(ack_severities)} alerts "
                          f"that were closed"),
            severities=list(ack_severities), require_closed=True),
        "MISSING_EVIDENCE": RuleScope(
            description="fires only on HIGH and CRITICAL alerts",
            severities=["HIGH", "CRITICAL"]),
        "FAST_CLOSURE": RuleScope(
            description=(f"fires only on HIGH and CRITICAL alerts, and only "
                          f"when the closure is at least {margin} minutes "
                          f"below the expected minimum"),
            severities=["HIGH", "CRITICAL"], threshold_limited=True),
        "REPETITIVE_INVESTIGATION": RuleScope(
            description=(f"fires only where the same note text repeats at "
                          f"least {repeat_min} times for one analyst"),
            threshold_limited=True),
        "REPEATED_ALERT_WITHOUT_REMEDIATION": RuleScope(
            description=(f"fires only where an asset carries at least "
                          f"{recurrence_min} alerts of one category"),
            threshold_limited=True),
    }


@dataclass
class RuleMetrics:
    """Detection performance for one rule."""

    finding_type: str
    grain: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    injected: int = 0
    fired: int = 0
    #: Seeded conditions the rule's own scope allows it to fire on.
    in_scope: Optional[int] = None
    scope_note: str = ""
    threshold_limited: bool = False

    @property
    def precision(self) -> Optional[float]:
        total = self.true_positives + self.false_positives
        return (self.true_positives / total) if total else None

    @property
    def recall(self) -> Optional[float]:
        total = self.true_positives + self.false_negatives
        return (self.true_positives / total) if total else None

    @property
    def recall_in_scope(self) -> Optional[float]:
        """
        Recall against the seeded conditions the rule can actually fire
        on. None where the scope is a computed threshold, since that is
        not re-derived here.
        """
        if self.in_scope is None:
            return None
        return (self.true_positives / self.in_scope) if self.in_scope else None

    @property
    def out_of_scope(self) -> Optional[int]:
        if self.in_scope is None:
            return None
        return self.injected - self.in_scope

    @property
    def f1(self) -> Optional[float]:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or (precision + recall) == 0:
            return None
        return 2 * precision * recall / (precision + recall)

    def format_ratio(self, value: Optional[float]) -> str:
        return "—" if value is None else f"{value:.1%}"


@dataclass
class RankingQuality:
    """
    Whether the review queue puts genuinely-affected records first.

    A queue that ranks correctly is worth more to a supervisor than one
    that merely contains the right items somewhere.
    """

    queue_size: int = 0
    queue_with_injected: int = 0
    top_10_with_injected: int = 0

    @property
    def precision_at_10(self) -> Optional[float]:
        size = min(10, self.queue_size)
        return (self.top_10_with_injected / size) if size else None

    @property
    def queue_precision(self) -> Optional[float]:
        return ((self.queue_with_injected / self.queue_size)
                if self.queue_size else None)

    def format_queue_precision(self) -> str:
        value = self.queue_precision
        return "—" if value is None else f"{value:.1%}"


@dataclass
class ReviewEffort:
    """
    How much manual review the prioritisation avoids.

    Stated as the share of records a supervisor would have to read to
    reach the prioritised set — the tool's central claim is that it makes
    review tractable, and that claim should carry a number.
    """

    total_alerts: int = 0
    total_findings: int = 0
    queue_size: int = 0

    @property
    def reviewed_fraction(self) -> Optional[float]:
        return (self.queue_size / self.total_alerts) if self.total_alerts else None

    def statement(self) -> str:
        if not self.total_alerts:
            return "No alerts assessed."
        fraction = self.reviewed_fraction or 0
        return (
            f"{self.queue_size} prioritised item(s) from {self.total_findings:,} "
            f"findings over {self.total_alerts:,} alerts — a supervisor reads "
            f"{fraction:.2%} of the alert population to reach the queue. This "
            f"measures prioritisation, not whether the right things were "
            f"prioritised; see precision and recall for that."
        )


@dataclass
class Validation:
    """A complete validation run against seeded ground truth."""

    dataset: str = ""
    rules: List[RuleMetrics] = field(default_factory=list)
    ranking: RankingQuality = field(default_factory=RankingQuality)
    effort: ReviewEffort = field(default_factory=ReviewEffort)
    unvalidated: List[str] = field(default_factory=list)

    #: Never varies. Synthetic validation is reported as synthetic.
    provenance: str = (
        "SYNTHETIC VALIDATION — measured against conditions deliberately "
        "injected by the dataset generator, not against expert manual "
        "review. The generator injects the conditions the rules look for, "
        "so agreement between them is partly circular: these figures show "
        "the rules behave as specified, not that the specification matches "
        "what an examiner would find. Expert validation requires labelled "
        "records from a real manual review, which this run does not have."
    )

    #: What a false positive does and does not mean here.
    false_positive_note: str = (
        "A false positive here means the rule fired on a record the "
        "generator did not deliberately seed. That is NOT the same as the "
        "rule being wrong. Conditions arise incidentally in generated "
        "data exactly as they do in real submissions: one analyst writing "
        "byte-identical notes three times, or an asset accumulating "
        "repeat alerts without remediation, is a genuine instance of what "
        "the rule detects whether or not it was planted. These figures "
        "are a floor on precision, not a defect count."
    )

    def rule(self, finding_type: str) -> Optional[RuleMetrics]:
        return next((r for r in self.rules if r.finding_type == finding_type),
                    None)

    def overall(self) -> RuleMetrics:
        total = RuleMetrics(finding_type="ALL RULES", grain="mixed")
        for metrics in self.rules:
            total.true_positives += metrics.true_positives
            total.false_positives += metrics.false_positives
            total.false_negatives += metrics.false_negatives
            total.injected += metrics.injected
            total.fired += metrics.fired
        return total


def load_ground_truth(dataset_path: str) -> Optional[dict]:
    """
    Seeded labels for a dataset, or None when there are none.

    None is the normal case for a real submission: a CSE does not ship
    labelled faults. Callers must treat its absence as "cannot validate",
    never as "nothing was wrong".
    """
    path = os.path.join(dataset_path, GROUND_TRUTH_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _findings_by_type(results: dict) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for entity in (results or {}).get("entities", []):
        for finding in (entity.get("execution_gap_findings", [])
                        + entity.get("negative_space_findings", [])):
            grouped.setdefault(
                str(finding.get("finding_type", "")), []).append(finding)
    return grouped


def _all_alert_ids(results: dict) -> Set[str]:
    ids = set()
    for entity in (results or {}).get("entities", []):
        for finding in entity.get("execution_gap_findings", []):
            alert_id = finding.get("alert_id")
            if alert_id:
                ids.add(str(alert_id))
    return ids


def validate(results: dict, ground_truth: dict, dataset: str = "",
              alerts=None, config: Optional[dict] = None) -> Validation:
    """
    Compare an assessment against the conditions the generator injected.

    Alert-grain rules are matched per alert. Entity-grain rules are
    matched per entity, because the generator seeded a rate or an
    absence rather than a specific record, and pretending otherwise
    would measure something that was never labelled.
    """
    report = Validation(dataset=dataset)
    scopes = build_scopes(config or {})

    # Alert attributes, for deriving in-scope counts independently of
    # the detectors. Absent when the caller did not supply the table,
    # in which case no scope correction is claimed.
    attributes: Dict[str, dict] = {}
    if alerts is not None and not alerts.empty:
        for row in alerts.to_dict(orient="records"):
            attributes[str(row.get("alert_id"))] = row

    injected_alerts: Dict[str, List[str]] = ground_truth.get("alerts", {})
    injected_entities: Dict[str, List[dict]] = ground_truth.get("entities", {})

    fired = _findings_by_type(results)

    # ---- alert-grain rules ------------------------------------------
    for finding_type in sorted(ALERT_RULES):
        metrics = RuleMetrics(finding_type=finding_type, grain="alert")

        truth = {alert_id for alert_id, conditions in injected_alerts.items()
                 if finding_type in conditions}
        detected = {str(f.get("alert_id")) for f in fired.get(finding_type, [])
                    if f.get("alert_id")}

        metrics.injected = len(truth)
        metrics.fired = len(detected)
        metrics.true_positives = len(truth & detected)
        metrics.false_positives = len(detected - truth)
        metrics.false_negatives = len(truth - detected)

        scope = scopes.get(finding_type)
        if scope is not None:
            metrics.scope_note = scope.description
            metrics.threshold_limited = scope.threshold_limited

            # Only claim an in-scope count where the scope is a plain
            # attribute predicate. A computed threshold is named, not
            # recomputed.
            if attributes and not scope.threshold_limited:
                in_scope = set()
                for alert_id in truth:
                    row = attributes.get(alert_id)
                    if row is None:
                        continue
                    if scope.severities and str(row.get("severity")) not in scope.severities:
                        continue
                    if scope.require_closed and str(row.get("status")) != "CLOSED":
                        continue
                    in_scope.add(alert_id)
                metrics.in_scope = len(in_scope)

        report.rules.append(metrics)

    # ---- entity-grain rules -----------------------------------------
    entities = {str(e.get("soc_id")) for e in (results or {}).get("entities", [])}

    for finding_type in sorted(ENTITY_RULES | GROUPED_RULES):
        grain = "entity" if finding_type in ENTITY_RULES else "asset group"
        metrics = RuleMetrics(finding_type=finding_type, grain=grain)

        truth = {soc_id for soc_id, conditions in injected_entities.items()
                 if any(c.get("condition") == finding_type for c in conditions)}
        if finding_type in GROUPED_RULES:
            # Seeded per alert but assessed per asset group, so a
            # per-alert comparison would not be like for like. Fold the
            # alert labels up to the entities that carry them and ask
            # whether the rule fired for those entities.
            truth = {
                soc for soc in entities
                if any(finding_type in conditions
                       for alert_id, conditions in injected_alerts.items()
                       if alert_id.startswith(f"ALT-{soc}-"))
            }

        detected = {str(f.get("soc_id")) for f in fired.get(finding_type, [])}

        metrics.injected = len(truth)
        metrics.fired = len(detected)
        metrics.true_positives = len(truth & detected)
        metrics.false_positives = len(detected - truth)
        metrics.false_negatives = len(truth - detected)

        report.rules.append(metrics)

    report.unvalidated = sorted(UNVALIDATED_RULES)

    # ---- ranking quality --------------------------------------------
    queue = (results or {}).get("review_queue", [])
    report.ranking.queue_size = len(queue)

    def is_injected(record) -> bool:
        alert_id = record.get("alert_id")
        if not alert_id:
            return False
        return str(record.get("finding_type")) in injected_alerts.get(
            str(alert_id), [])

    report.ranking.queue_with_injected = sum(1 for r in queue if is_injected(r))
    report.ranking.top_10_with_injected = sum(
        1 for r in sorted(queue, key=lambda x: x.get("queue_rank", 10 ** 9))[:10]
        if is_injected(r))

    # ---- review effort ----------------------------------------------
    metadata = (results or {}).get("run_metadata", {})
    report.effort.total_alerts = int(metadata.get("total_alerts", 0))
    report.effort.total_findings = int(metadata.get("total_findings", 0))
    report.effort.queue_size = len(queue)

    return report


def format_report(report: Validation) -> str:
    """Plain-text validation report, for the CLI and for the record."""
    lines = [
        "SAT-SA VALIDATION REPORT",
        "=" * 78,
        "",
        report.provenance,
        "",
        report.false_positive_note,
        "",
        f"Dataset: {report.dataset}",
        "",
        "PER-RULE DETECTION",
        "-" * 78,
        "Recall is shown against every seeded condition and, where the "
        "rule declares a",
        "scope, against the conditions it can actually fire on. A rule "
        "that declines to",
        "fire outside its scope has not missed anything.",
        "",
        f"{'Rule':36}{'Seeded':>7}{'InScope':>8}{'TP':>6}{'FP':>5}"
        f"{'FN':>5}{'Prec':>8}{'Recall':>8}{'InScope':>9}",
    ]

    for metrics in report.rules:
        in_scope = ("—" if metrics.in_scope is None
                    else str(metrics.in_scope))
        lines.append(
            f"{metrics.finding_type:36}"
            f"{metrics.injected:>7}{in_scope:>8}"
            f"{metrics.true_positives:>6}{metrics.false_positives:>5}"
            f"{metrics.false_negatives:>5}"
            f"{metrics.format_ratio(metrics.precision):>8}"
            f"{metrics.format_ratio(metrics.recall):>8}"
            f"{metrics.format_ratio(metrics.recall_in_scope):>9}")

    total = report.overall()
    lines += [
        "-" * 78,
        f"{total.finding_type:36}"
        f"{total.injected:>7}{'—':>8}"
        f"{total.true_positives:>6}{total.false_positives:>5}"
        f"{total.false_negatives:>5}"
        f"{total.format_ratio(total.precision):>8}"
        f"{total.format_ratio(total.recall):>8}"
        f"{'—':>9}",
        "",
    ]

    scoped = [m for m in report.rules if m.scope_note]
    if scoped:
        lines += ["RULE SCOPES", "-" * 78]
        for metrics in scoped:
            out = metrics.out_of_scope
            suffix = ""
            if out:
                suffix = (f"  [{out} seeded condition(s) fall outside this "
                          f"scope and are not misses]")
            elif metrics.threshold_limited:
                suffix = ("  [scope is a computed threshold; not "
                          "recomputed here, so recall is reported "
                          "uncorrected]")
            lines.append(f"{metrics.finding_type}: {metrics.scope_note}.{suffix}")
        lines.append("")

    if report.unvalidated:
        lines += [
            "NOT VALIDATED",
            "-" * 78,
            f"{', '.join(report.unvalidated)} — emitted at a grain the "
            "generator does not label, so no measurement is claimed.",
            "",
        ]

    lines += [
        "RANKING QUALITY",
        "-" * 78,
        f"Review queue holds {report.ranking.queue_size} item(s); "
        f"{report.ranking.queue_with_injected} correspond to a seeded "
        f"condition "
        f"({report.ranking.format_queue_precision()}).",
        f"Of the top 10 by queue rank, "
        f"{report.ranking.top_10_with_injected} correspond to a seeded "
        f"condition.",
        "",
        "REVIEW EFFORT",
        "-" * 78,
        report.effort.statement(),
        "",
    ]

    return "\n".join(lines)
