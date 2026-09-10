"""
Safe ZIP extraction for untrusted supervisory submissions.

`ZipFile.extractall` is not usable here. Python's own documentation
warns that extracting an untrusted archive without inspection can write
files outside the destination, and `extractall` additionally has no
notion of a size budget, so a zip bomb succeeds until the disk is full.

Every member is therefore checked BEFORE anything is written:

  * the resolved destination must stay inside the extraction root
    (blocks `../..` traversal and absolute member paths, including
    Windows drive-letter and UNC forms that os.path.isabs misses on
    POSIX)
  * symlinks and every other non-regular entry are refused, since a
    symlink member is the standard way to escape a extraction root
    after the path check passes
  * declared sizes are summed against a total budget and a per-member
    budget before extraction, and the actual bytes written are counted
    as they are streamed, because the declared size in a ZIP header is
    attacker-controlled and can lie
  * the overall compression ratio is bounded
  * only data-file extensions are extracted at all

Nothing in a submission is ever executed, imported, or evaluated.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from typing import List, Optional

from analytics.ingestion.errors import IngestionError, UnsafeArchiveError
from analytics.ingestion.limits import DEFAULT_LIMITS, IngestionLimits, human_bytes

#: Read in chunks so a lying size header cannot force a huge allocation.
CHUNK_BYTES = 1024 * 1024


def _is_unsafe_path(name: str) -> Optional[str]:
    """Return a reason the member path is unsafe, or None if it is fine."""
    if not name or name in (".", ".."):
        return "empty or relative-only member name"

    # Null bytes and control characters. These are currently rejected
    # further down as a side effect of extension filtering, but relying
    # on that is fragile: a name like "alerts\x00.csv" can be truncated
    # at the null by a C-level filesystem call while Python's own checks
    # saw the full string. Reject them outright.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return "control character in member name"

    normalized = name.replace("\\", "/")

    if normalized.startswith("/"):
        return "absolute path"

    # Windows-style absolute paths ("C:/x") and UNC paths ("//host/share")
    # are not absolute under POSIX os.path rules, so check them directly.
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        return "drive-letter absolute path"
    if normalized.startswith("//"):
        return "UNC network path"

    if any(part == ".." for part in normalized.split("/")):
        return "parent-directory traversal ('..')"

    return None


def _member_is_regular_file(info: zipfile.ZipInfo) -> bool:
    """
    False for directories, symlinks, devices, and anything else that is
    not a plain file. The upper 16 bits of external_attr carry the Unix
    mode when the archive was produced on a Unix system.
    """
    if info.is_dir():
        return False

    mode = info.external_attr >> 16
    if mode:
        file_type = mode & 0o170000
        # S_IFREG is 0o100000. A zero file type means the producer did
        # not record one, which is normal for Windows-made archives.
        if file_type and file_type != 0o100000:
            return False

    return True


def inspect_archive(archive_path: str,
                     limits: IngestionLimits = DEFAULT_LIMITS) -> List[zipfile.ZipInfo]:
    """
    Validate an archive and return the members that may be extracted.

    Raises before any byte is written to disk.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise IngestionError(
                    f"The archive is corrupt — '{bad}' failed its checksum.",
                    "Ask the entity to resend the submission.",
                    archive_path)

            members = archive.infolist()
    except zipfile.BadZipFile:
        raise IngestionError(
            "This file is not a readable ZIP archive.",
            "Confirm the submission was not truncated in transfer.",
            archive_path)
    except OSError as exc:
        raise IngestionError(
            f"The archive could not be opened: {exc}", source=archive_path)

    if len(members) > limits.max_members:
        raise UnsafeArchiveError(
            f"The archive contains {len(members):,} entries, above the "
            f"{limits.max_members:,} limit.",
            "A supervisory submission should not contain this many files.",
            archive_path)

    selected: List[zipfile.ZipInfo] = []
    total_uncompressed = 0
    total_compressed = 0

    for info in members:
        reason = _is_unsafe_path(info.filename)
        if reason is not None:
            raise UnsafeArchiveError(
                f"The archive contains an unsafe member path "
                f"({reason}): '{info.filename}'.",
                "This archive was refused because extracting it could "
                "write outside the intended folder. Do not use this "
                "submission without confirming its origin.",
                archive_path)

        if info.is_dir():
            continue

        if not _member_is_regular_file(info):
            raise UnsafeArchiveError(
                f"The archive contains a link or special file: "
                f"'{info.filename}'.",
                "Only plain data files are accepted in a submission.",
                archive_path)

        if info.file_size > limits.max_member_uncompressed_bytes:
            raise UnsafeArchiveError(
                f"'{info.filename}' expands to "
                f"{human_bytes(info.file_size)}, above the per-file limit "
                f"of {human_bytes(limits.max_member_uncompressed_bytes)}.",
                source=archive_path)

        total_uncompressed += info.file_size
        total_compressed += info.compress_size

        if os.path.splitext(info.filename)[1].lower() in limits.allowed_member_suffixes:
            selected.append(info)

    if total_uncompressed > limits.max_total_uncompressed_bytes:
        raise UnsafeArchiveError(
            f"The archive expands to "
            f"{human_bytes(total_uncompressed)}, above the "
            f"{human_bytes(limits.max_total_uncompressed_bytes)} limit.",
            source=archive_path)

    if total_compressed > 0:
        ratio = total_uncompressed / total_compressed
        if ratio > limits.max_compression_ratio:
            raise UnsafeArchiveError(
                f"The archive expands {ratio:,.0f}x its compressed size, "
                f"above the {limits.max_compression_ratio:,}x limit.",
                "An expansion ratio this high indicates a crafted archive "
                "rather than a data submission.",
                archive_path)

    if not selected:
        raise IngestionError(
            "The archive contains no CSV, JSON, or database files.",
            "A submission should contain the SOC tables as CSV or JSON.",
            archive_path)

    return selected


def extract_archive(archive_path: str, destination: str,
                     limits: IngestionLimits = DEFAULT_LIMITS) -> str:
    """
    Extract the safe members of `archive_path` into `destination`.

    Returns the directory the dataset actually lives in — descending
    through a single wrapper folder, which is what most archives have
    because they were made by zipping a directory rather than its
    contents.
    """
    members = inspect_archive(archive_path, limits)
    root = os.path.realpath(destination)
    os.makedirs(root, exist_ok=True)

    written = 0

    with zipfile.ZipFile(archive_path) as archive:
        for info in members:
            target = os.path.realpath(os.path.join(root, info.filename))

            # Re-check after resolution. The name passed the textual
            # check above; this catches anything the filesystem does to
            # it, and is the check that actually matters.
            if target != root and not target.startswith(root + os.sep):
                raise UnsafeArchiveError(
                    f"Member '{info.filename}' resolves outside the "
                    "extraction folder.",
                    "This archive was refused on safety grounds.",
                    archive_path)

            os.makedirs(os.path.dirname(target), exist_ok=True)

            # Stream with a running byte count. The header's file_size
            # was already budgeted, but it is attacker-controlled and
            # may understate the real content, so the true total is
            # enforced here as bytes actually arrive.
            with archive.open(info) as source, open(target, "wb") as sink:
                while True:
                    chunk = source.read(CHUNK_BYTES)
                    if not chunk:
                        break

                    written += len(chunk)
                    if written > limits.max_total_uncompressed_bytes:
                        sink.close()
                        shutil.rmtree(root, ignore_errors=True)
                        raise UnsafeArchiveError(
                            "The archive's real contents exceed the "
                            f"{human_bytes(limits.max_total_uncompressed_bytes)} "
                            "extraction limit; its declared sizes were "
                            "understated.",
                            "This is characteristic of a crafted archive.",
                            archive_path)

                    sink.write(chunk)

    return descend_single_wrapper(root)


def descend_single_wrapper(directory: str) -> str:
    """
    Follow a chain of folders that each contain exactly one folder and
    nothing else, so `submission.zip` holding `submission/alerts.csv`
    resolves to the folder with the tables in it.
    """
    current = directory
    for _ in range(8):  # bounded; a real submission nests once, maybe twice
        entries = [e for e in os.listdir(current) if not e.startswith("__MACOSX")]
        if len(entries) != 1:
            return current
        only = os.path.join(current, entries[0])
        if not os.path.isdir(only):
            return current
        current = only
    return current


def extract_to_temp(archive_path: str,
                     limits: IngestionLimits = DEFAULT_LIMITS):
    """
    Extract into a fresh temporary directory.

    Returns (temp_root, dataset_dir). Both are returned because they
    differ whenever the archive wraps its contents in a folder, and the
    caller must delete the ROOT — deriving it by walking up from the
    dataset directory is guesswork that breaks on an unexpected layout.
    """
    temp_root = tempfile.mkdtemp(prefix="satsa-ingest-")
    try:
        return temp_root, extract_archive(archive_path, temp_root, limits)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
