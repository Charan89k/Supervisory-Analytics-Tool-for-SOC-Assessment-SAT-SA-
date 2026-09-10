"""
Source-record retrieval for the Findings explorer.

The audit chain a supervisory finding must support is:

    Finding -> Rule -> Rationale -> Evidence -> Source records

The first four are produced by the rule engine and travel in the
assessment output. The fifth is not: source records stay in the
submission, and returning them means reading the submission again.

This service does that lazily — nothing is loaded until a supervisor
actually drills into a finding — and caches the result for the session,
because the alternative (embedding every row behind every finding in
assessment_results.json) would multiply the output many times over for
records the reader will mostly never open.

It reinterprets nothing. Rows are returned exactly as they appear in
the submission; the rule's own evidence dict remains the authoritative
statement of what the rule read.
"""

from __future__ import annotations

from typing import Optional

from analytics.evidence import evidence_bundle_for_finding
from analytics.ingestion import IngestionError
from analytics.loader import load_soc_dataset
from analytics.normalizer import build_alerts_enriched, normalize_dataset


class SourceUnavailable(Exception):
    """
    The submission behind an assessment can no longer be read.

    Distinct from "this finding has no source records", which never
    happens. If a supervisor cannot reach the source, they must be told
    the submission moved — not shown an empty panel that reads as
    "there was no evidence".
    """


class EvidenceService:
    """Lazily re-reads the submission to serve source records."""

    def __init__(self):
        self._dataset_path: Optional[str] = None
        self._data = None
        self._alerts_enriched = None

    @property
    def loaded_path(self) -> Optional[str]:
        return self._dataset_path if self._data is not None else None

    def is_loaded_for(self, dataset_path: str) -> bool:
        return self._data is not None and self._dataset_path == dataset_path

    def load(self, dataset_path: str) -> None:
        """
        Read and normalise the submission. Idempotent for a given path,
        so repeated drill-downs pay the cost once.
        """
        if self.is_loaded_for(dataset_path):
            return

        if not dataset_path:
            raise SourceUnavailable(
                "No dataset is associated with this assessment, so its "
                "source records cannot be retrieved.")

        try:
            data = normalize_dataset(load_soc_dataset(dataset_path))
            alerts_enriched = build_alerts_enriched(data)
        except IngestionError as exc:
            raise SourceUnavailable(
                f"The submission could not be re-read for drill-down.\n\n"
                f"{exc.message()}") from exc
        except Exception as exc:
            raise SourceUnavailable(
                f"The submission could not be re-read for drill-down:\n"
                f"{exc}") from exc

        self._dataset_path = dataset_path
        self._data = data
        self._alerts_enriched = alerts_enriched

    def bundle_for(self, finding: dict, dataset_path: str) -> dict:
        """Source records behind one finding, at whatever grain it has."""
        self.load(dataset_path)
        return evidence_bundle_for_finding(
            self._data, self._alerts_enriched, finding)

    def reset(self) -> None:
        """Drop the cache — a new assessment may use a different submission."""
        self._dataset_path = None
        self._data = None
        self._alerts_enriched = None
