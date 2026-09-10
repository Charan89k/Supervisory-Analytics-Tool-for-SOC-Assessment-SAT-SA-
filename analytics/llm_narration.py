"""
Offline Qwen narration layer for SAT-SA.

ARCHITECTURAL RULE (do not violate): Qwen never decides whether
something is a finding, never changes a finding_type, a rule_id, a
severity, or a risk score, and never sees raw unlabelled data. It is
handed a finding that the deterministic rule engine already produced
— finding_type, rule_id, rationale, evidence — and asked only to
restate it in clearer prose for a human reviewer, and optionally
suggest a remediation action. If narration fails or is disabled, the
pipeline falls back to the rule-generated `rationale` untouched: the
system is fully usable with this layer off.

Talks to a local Ollama instance running Qwen (offline, no external
API calls) via its HTTP API. Never raises on failure — narration is
a nice-to-have UX layer, not a dependency the pipeline can break on.
"""

import json

import requests


PROMPT_TEMPLATE = """You are a supervisory-analytics assistant. You are given ONE finding \
that a deterministic rule engine has already decided is real. Do not \
second-guess, re-score, or add new claims. Restate it clearly for a \
human SOC supervisor in 2-3 sentences, then suggest one concrete next \
review step. Stay strictly within the evidence given.

finding_type: {finding_type}
rule_id: {rule_id}
severity: {severity}
rule_rationale: {rationale}
evidence: {evidence}

Respond with plain text only, no headers, no markdown."""


def narrate_finding(finding: dict, cfg: dict) -> str:
    """
    Returns a narrated explanation for one finding dict, or the
    original rule-generated rationale if narration is disabled,
    unavailable, or fails for any reason.
    """
    llm_cfg = cfg.get("llm_narration", {})
    fallback = finding.get("rationale", "")

    if not llm_cfg.get("enabled", False):
        return fallback

    base_url = llm_cfg.get("base_url", "http://localhost:11434")
    model = llm_cfg.get("model", "qwen2.5:14b")
    # CPU inference of a 14B model can genuinely take 1-3+ minutes per
    # call. A short timeout here doesn't make narration faster — it
    # just silently discards slow-but-valid responses as failures and
    # falls back to the rule rationale with no visible error. Default
    # kept generous; lower it only if you've confirmed GPU inference.
    timeout = llm_cfg.get("timeout_seconds", 180)

    prompt = PROMPT_TEMPLATE.format(
        finding_type=finding.get("finding_type", ""),
        rule_id=finding.get("rule_id", ""),
        severity=finding.get("severity", "N/A"),
        rationale=fallback,
        evidence=json.dumps(finding.get("evidence", {}), default=str),
    )

    try:
        response = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        response.raise_for_status()
        narrated = response.json().get("response", "").strip()
        return narrated if narrated else fallback
    except Exception:
        # Offline model unreachable, not installed, or errored — the
        # pipeline degrades gracefully to the deterministic rationale.
        return fallback


def narrate_queue(review_queue_records: list, cfg: dict) -> list:
    """
    Adds a `narrated_explanation` field to each review-queue record
    (list of dicts, e.g. from a DataFrame.to_dict('records') call).
    Leaves every other field untouched — this only adds prose, never
    edits finding_type, rule_id, severity, or evidence.
    """
    llm_cfg = cfg.get("llm_narration", {})
    if not llm_cfg.get("enabled", False):
        for record in review_queue_records:
            record["narrated_explanation"] = record.get("rationale", "")
        return review_queue_records

    for record in review_queue_records:
        record["narrated_explanation"] = narrate_finding(record, cfg)
    return review_queue_records
