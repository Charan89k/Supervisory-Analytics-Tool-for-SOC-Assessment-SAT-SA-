"""
Resource limits applied to every untrusted submission.

These exist because a dataset is attacker-controllable input. Without
them, a 200 KB archive can exhaust the disk of the machine running the
assessment, and a crafted member path can write outside the extraction
directory. The defaults are generous enough for a real multi-entity
submission and small enough that a hostile one fails fast.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IngestionLimits:
    #: Total uncompressed bytes an archive may expand to. A realistic
    #: multi-entity CSV submission is tens to hundreds of MB.
    max_total_uncompressed_bytes: int = 2 * 1024 ** 3      # 2 GiB

    #: Any single member larger than this is rejected on its own.
    max_member_uncompressed_bytes: int = 1 * 1024 ** 3     # 1 GiB

    #: Guards against an archive with an enormous number of entries,
    #: which is a denial-of-service vector even when each is tiny.
    max_members: int = 10_000

    #: Zip-bomb guard. A 1000:1 ratio does not occur in CSV or JSON
    #: submissions; highly repetitive text tops out well below this.
    #: Applied to the archive as a whole, not per member, so a single
    #: well-compressed file cannot trip it on its own.
    max_compression_ratio: int = 1000

    #: Largest single non-archive file (JSON submission, SQLite export).
    max_file_bytes: int = 2 * 1024 ** 3                    # 2 GiB

    #: Extensions an archive member may have. Anything else is skipped,
    #: so an archive carrying scripts or binaries contributes nothing.
    #: SAT-SA never executes a dataset member under any circumstances;
    #: this narrows what even reaches a parser.
    allowed_member_suffixes: frozenset = frozenset(
        {".csv", ".json", ".sqlite", ".db", ".sqlite3"}
    )


DEFAULT_LIMITS = IngestionLimits()


def human_bytes(value: int) -> str:
    step = 1024.0
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < step or unit == "GB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= step
    return f"{amount:.1f} GB"
