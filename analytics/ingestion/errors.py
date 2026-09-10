"""
Ingestion failures that a supervisor — not a developer — has to read.

A submission arrives from an external entity and is untrusted input. It
will sometimes be malformed, truncated, wrongly packaged, or hostile.
None of those may surface as a raw OSError, KeyError, or stack trace:
the person holding the failure is a supervisor deciding whether to ask
the entity for a corrected submission, and they need to know what was
wrong with it.
"""


class IngestionError(Exception):
    """
    A dataset could not be read. Carries a message written for the
    person who has to act on it, plus an optional remedy line.
    """

    def __init__(self, message: str, remedy: str = "", source: str = ""):
        self.raw_message = message
        self.remedy = remedy
        self.source = source
        super().__init__(self.message())

    def message(self) -> str:
        parts = [self.raw_message]
        if self.source:
            parts.append(f"\nSource: {self.source}")
        if self.remedy:
            parts.append(f"\n{self.remedy}")
        return "".join(parts)


class UnsafeArchiveError(IngestionError):
    """
    An archive was rejected on safety grounds rather than on content.

    Kept distinct from a plain IngestionError because these are the
    cases worth noticing: a submission that tries to write outside its
    extraction directory, or that expands to far more than it claims,
    is not simply a corrupt file.
    """
