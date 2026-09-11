"""
Application settings outside the AI layer.

Every field here changes something the application actually does. A
setting that looks configurable but has no effect is worse than no
setting at all: it tells a supervisor they have control they do not
have, and the first time it matters they will have relied on it.

  history_directory      -> where HistoryService stores runs
  reopen_last_page       -> whether the window restores its last page
  generate_csv_exports   -> run_pipeline(export_csv=...)
  generate_pdf_report    -> run_pipeline(export_pdf=...)
  strict_validation      -> whether warnings block an assessment
  max_archive_gigabytes  -> IngestionLimits.max_total_uncompressed_bytes
  max_archive_members    -> IngestionLimits.max_members

The read-only System information the page shows is derived at display
time rather than stored, so it can never go stale against the running
application.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from analytics.ingestion.limits import DEFAULT_LIMITS, IngestionLimits

GIGABYTE = 1024 ** 3


@dataclass
class AppSettings:
    # -- General -----------------------------------------------------
    #: Empty means "use the default location", which keeps a stored
    #: settings file portable between machines.
    history_directory: str = ""
    reopen_last_page: bool = True

    # -- Reports -----------------------------------------------------
    generate_csv_exports: bool = True
    generate_pdf_report: bool = True

    # -- Data --------------------------------------------------------
    #: Warnings never block by default. The validator's contract is to
    #: report everything wrong with a submission so a supervisor can
    #: send one complete list of corrections back; blocking on a
    #: warning by default would stop assessments that are perfectly
    #: analysable. A supervisor who wants a stricter gate can set it.
    strict_validation: bool = False
    max_archive_gigabytes: float = 2.0
    max_archive_members: int = 10_000

    def ingestion_limits(self) -> IngestionLimits:
        """The configured limits, as the ingestion layer expects them."""
        total = int(self.max_archive_gigabytes * GIGABYTE)
        return IngestionLimits(
            max_total_uncompressed_bytes=total,
            # A single member may not exceed the whole-archive budget.
            max_member_uncompressed_bytes=min(
                DEFAULT_LIMITS.max_member_uncompressed_bytes, total),
            max_members=int(self.max_archive_members),
            max_compression_ratio=DEFAULT_LIMITS.max_compression_ratio,
            max_file_bytes=total,
            allowed_member_suffixes=DEFAULT_LIMITS.allowed_member_suffixes,
        )

    def to_dict(self) -> dict:
        return asdict(self)
