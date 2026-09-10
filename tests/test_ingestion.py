"""
Ingestion tests.

A submission is untrusted input from an external entity. Roughly half of
these tests are adversarial: they build genuinely malicious archives and
assert that nothing is written outside the extraction root, that a zip
bomb is refused before it fills the disk, and that every failure reaches
the caller as a supervisor-readable IngestionError rather than a raw
OSError or a stack trace.

The other half assert the property that makes the adapter layer worth
having: every supported format resolves to the SAME internal model, so
the analytics engine cannot tell them apart.
"""

import json
import os
import sqlite3
import stat
import zipfile

import pandas as pd
import pytest

from analytics.ingestion import (
    IngestionError,
    UnsafeArchiveError,
    accepts_files,
    classify_input,
    describe_supported_inputs,
    dialog_filter,
    rejection_reason,
    resolve_dataset,
)
from analytics.ingestion.limits import IngestionLimits
from analytics.ingestion.safe_archive import (
    descend_single_wrapper,
    extract_to_temp,
    inspect_archive,
)
from analytics.ingestion.schema import OPTIONAL_TABLES, REQUIRED_TABLES, TABLES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(DATA),
    reason="synthetic dataset not generated",
)


# ---------------------------------------------------------------------------
# Fixtures: the same submission in every supported format
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tables():
    return {t: pd.read_csv(os.path.join(DATA, f"{t}.csv"))
            for t in TABLES if os.path.exists(os.path.join(DATA, f"{t}.csv"))}


def _records(frame):
    return frame.astype(object).where(pd.notnull(frame), None).to_dict("records")


@pytest.fixture(scope="module")
def zip_submission(tables, tmp_path_factory):
    """Wrapped in a folder, the way a real zipped submission arrives."""
    path = tmp_path_factory.mktemp("zip") / "submission.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for table in tables:
            archive.write(os.path.join(DATA, f"{table}.csv"),
                          f"submission_2026Q3/{table}.csv")
    return str(path)


@pytest.fixture(scope="module")
def json_submission(tables, tmp_path_factory):
    path = tmp_path_factory.mktemp("json") / "submission.json"
    payload = {t: _records(df) for t, df in tables.items()}
    path.write_text(json.dumps(payload, default=str))
    return str(path)


@pytest.fixture(scope="module")
def json_directory(tables, tmp_path_factory):
    directory = tmp_path_factory.mktemp("jsondir")
    for table, frame in tables.items():
        (directory / f"{table}.json").write_text(
            json.dumps(_records(frame), default=str))
    return str(directory)


@pytest.fixture(scope="module")
def sqlite_submission(tables, tmp_path_factory):
    path = tmp_path_factory.mktemp("sqlite") / "submission.sqlite"
    connection = sqlite3.connect(path)
    for table, frame in tables.items():
        frame.to_sql(table, connection, index=False, if_exists="replace")
    connection.close()
    return str(path)


# ---------------------------------------------------------------------------
# Every format resolves to the same internal model
# ---------------------------------------------------------------------------

def test_csv_directory_loads():
    dataset = resolve_dataset(DATA)
    assert set(dataset) == set(TABLES)
    assert len(dataset["alerts"]) == 3296


@pytest.mark.parametrize("fixture_name", [
    "zip_submission", "json_submission", "json_directory", "sqlite_submission",
])
def test_every_format_matches_the_csv_baseline(fixture_name, request):
    """
    The property the adapter layer exists to provide. If any format
    produced a different row count or table set, the analytics engine
    would silently assess a different submission depending on how the
    entity chose to package it.
    """
    baseline = resolve_dataset(DATA)
    candidate = resolve_dataset(request.getfixturevalue(fixture_name))

    assert set(candidate) == set(baseline)
    for table in TABLES:
        assert len(candidate[table]) == len(baseline[table]), table


def test_zip_descends_into_its_wrapper_folder(zip_submission):
    """submission.zip holding submission_2026Q3/alerts.csv must just work."""
    assert len(resolve_dataset(zip_submission)["alerts"]) == 3296


def test_zip_extraction_leaves_no_temporary_files(zip_submission, tmp_path):
    import glob
    import tempfile
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "satsa-ingest-*")))
    resolve_dataset(zip_submission)
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "satsa-ingest-*")))
    assert after == before, "extraction temp directory was not cleaned up"


def test_sqlite_is_opened_read_only(sqlite_submission):
    """Assessing a submission must not modify it."""
    before = os.path.getmtime(sqlite_submission)
    resolve_dataset(sqlite_submission)
    assert os.path.getmtime(sqlite_submission) == before


# ---------------------------------------------------------------------------
# Archive safety
# ---------------------------------------------------------------------------

def _archive(path, builder, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression) as archive:
        builder(archive)
    return str(path)


@pytest.mark.parametrize("name,member", [
    ("parent traversal", "../../../../tmp/satsa-pwned.csv"),
    ("absolute path", "/tmp/satsa-pwned.csv"),
    ("drive letter", "C:/Windows/satsa-pwned.csv"),
    ("unc path", "//host/share/satsa-pwned.csv"),
    ("backslash traversal", "..\\..\\satsa-pwned.csv"),
    ("nested traversal", "data/../../satsa-pwned.csv"),
])
def test_path_traversal_is_refused(name, member, tmp_path):
    path = _archive(tmp_path / "evil.zip",
                     lambda a: a.writestr(member, "alert_id\n1\n"))

    with pytest.raises(UnsafeArchiveError) as excinfo:
        extract_to_temp(path)

    assert "unsafe member path" in excinfo.value.raw_message
    assert not os.path.exists("/tmp/satsa-pwned.csv"), (
        f"{name} escaped the extraction root")


def test_symlink_member_is_refused(tmp_path):
    """
    A symlink member is the standard way out of an extraction root once
    the textual path check passes.
    """
    def build(archive):
        info = zipfile.ZipInfo("alerts.csv")
        info.create_system = 3  # Unix
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")

    with pytest.raises(UnsafeArchiveError, match="link or special file"):
        extract_to_temp(_archive(tmp_path / "link.zip", build))


def test_zip_bomb_ratio_is_refused(tmp_path):
    path = _archive(tmp_path / "bomb.zip",
                     lambda a: a.writestr("alerts.csv", "A" * (60 * 1024 * 1024)))

    with pytest.raises(UnsafeArchiveError, match="expands"):
        extract_to_temp(path)


def test_member_count_limit_is_enforced(tmp_path):
    def build(archive):
        for i in range(60):
            archive.writestr(f"file{i}.csv", "x")

    path = _archive(tmp_path / "many.zip", build)
    limits = IngestionLimits(max_members=50)

    with pytest.raises(UnsafeArchiveError, match="entries"):
        inspect_archive(path, limits)


def test_total_size_limit_is_enforced(tmp_path):
    path = _archive(tmp_path / "big.zip",
                     lambda a: a.writestr("alerts.csv", "A" * 200_000))
    limits = IngestionLimits(max_total_uncompressed_bytes=1000,
                              max_compression_ratio=10 ** 9)

    with pytest.raises(UnsafeArchiveError, match="expands to"):
        inspect_archive(path, limits)


def test_per_member_size_limit_is_enforced(tmp_path):
    path = _archive(tmp_path / "bigmember.zip",
                     lambda a: a.writestr("alerts.csv", "A" * 200_000))
    limits = IngestionLimits(max_member_uncompressed_bytes=1000,
                              max_compression_ratio=10 ** 9)

    with pytest.raises(UnsafeArchiveError, match="per-file limit"):
        inspect_archive(path, limits)


def test_executable_members_are_not_extracted(tmp_path):
    """
    A submission carrying a script or binary contributes nothing: only
    data extensions are extracted at all. SAT-SA never executes a
    dataset member regardless, but the parser should not even see them.
    """
    def build(archive):
        archive.writestr("submission/alerts.csv", "alert_id\nA1\n")
        archive.writestr("submission/payload.sh", "#!/bin/sh\nrm -rf /\n")
        archive.writestr("submission/payload.exe", "MZ\x90\x00")

    temp_root, directory = extract_to_temp(
        _archive(tmp_path / "mixed.zip", build))
    try:
        extracted = os.listdir(directory)
        assert "alerts.csv" in extracted
        assert not any(f.endswith((".sh", ".exe")) for f in extracted), extracted
    finally:
        import shutil
        shutil.rmtree(temp_root, ignore_errors=True)


def test_archive_with_no_data_files_is_rejected(tmp_path):
    path = _archive(tmp_path / "empty.zip",
                     lambda a: a.writestr("README.txt", "hello"))
    with pytest.raises(IngestionError, match="no CSV, JSON, or database"):
        extract_to_temp(path)


def test_corrupt_archive_is_reported_clearly(tmp_path):
    path = tmp_path / "corrupt.zip"
    path.write_bytes(b"PK\x03\x04 this is not really a zip file")
    with pytest.raises(IngestionError, match="not a readable ZIP"):
        resolve_dataset(str(path))


def test_descend_stops_when_a_folder_holds_more_than_one_entry(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    (tmp_path / "b.csv").write_text("x")
    assert descend_single_wrapper(str(tmp_path)) == str(tmp_path)


# ---------------------------------------------------------------------------
# Malformed content
# ---------------------------------------------------------------------------

def test_malformed_json_reports_line_and_column(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"alerts": [{"alert_id": "A1"},,]}')
    with pytest.raises(IngestionError) as excinfo:
        resolve_dataset(str(path))
    assert "not valid JSON" in excinfo.value.raw_message
    assert "line" in excinfo.value.raw_message


def test_json_that_is_a_list_is_rejected_with_the_expected_shape(tmp_path):
    path = tmp_path / "list.json"
    path.write_text('[{"alert_id": "A1"}]')
    with pytest.raises(IngestionError) as excinfo:
        resolve_dataset(str(path))
    assert "object mapping table names" in excinfo.value.raw_message
    assert '"alerts"' in excinfo.value.message()


def test_json_table_of_wrong_type_is_rejected(tmp_path):
    path = tmp_path / "wrong.json"
    path.write_text('{"alerts": "not a list"}')
    with pytest.raises(IngestionError, match="must be a list of records"):
        resolve_dataset(str(path))


def test_json_rows_that_are_not_objects_are_rejected(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text('{"alerts": [1, 2, 3]}')
    with pytest.raises(IngestionError, match="not objects"):
        resolve_dataset(str(path))


def test_non_utf8_csv_is_reported_clearly(tmp_path):
    dataset = tmp_path / "latin"
    dataset.mkdir()
    for table in REQUIRED_TABLES:
        (dataset / f"{table}.csv").write_text("id\n1\n")
    (dataset / "alerts.csv").write_bytes(b"alert_id,name\nA1,caf\xe9\n")

    with pytest.raises(IngestionError, match="not valid UTF-8"):
        resolve_dataset(str(dataset))


def test_a_file_that_is_not_a_database_is_reported_clearly(tmp_path):
    path = tmp_path / "fake.sqlite"
    path.write_text("this is plainly not a database")
    with pytest.raises(IngestionError):
        resolve_dataset(str(path))


def test_json_wrapper_key_is_tolerated(tmp_path):
    """Database exporters commonly wrap the payload in {"tables": {...}}."""
    tables = {t: [] for t in REQUIRED_TABLES}
    tables["alerts"] = [{"alert_id": "A1"}]
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"tables": tables}))
    dataset = resolve_dataset(str(path))
    assert len(dataset["alerts"]) == 1


# ---------------------------------------------------------------------------
# Schema requirements
# ---------------------------------------------------------------------------

def test_missing_required_table_is_named_in_the_error(tmp_path):
    dataset = tmp_path / "partial"
    dataset.mkdir()
    for table in REQUIRED_TABLES:
        if table != "telemetry":
            (dataset / f"{table}.csv").write_text("id\n1\n")

    with pytest.raises(IngestionError) as excinfo:
        resolve_dataset(str(dataset))
    assert "telemetry" in excinfo.value.raw_message


def test_optional_tables_may_be_absent(tmp_path):
    dataset = tmp_path / "minimal"
    dataset.mkdir()
    for table in REQUIRED_TABLES:
        (dataset / f"{table}.csv").write_text("id\n1\n")

    result = resolve_dataset(str(dataset))
    for table in OPTIONAL_TABLES:
        assert result[table].empty


def test_empty_required_table_is_left_for_the_validator(tmp_path):
    """
    An empty-but-present table is a validation concern, not an ingestion
    failure. The validator reports every problem in one report; failing
    fast here would reduce that to "the first problem we hit".
    """
    dataset = tmp_path / "emptytable"
    dataset.mkdir()
    for table in REQUIRED_TABLES:
        (dataset / f"{table}.csv").write_text("id\n1\n")
    (dataset / "telemetry.csv").write_text("telemetry_id,soc_id\n")

    result = resolve_dataset(str(dataset))
    assert result["telemetry"].empty


# ---------------------------------------------------------------------------
# The claim/capability boundary
# ---------------------------------------------------------------------------

def test_supported_formats_are_all_actually_loadable(
        zip_submission, json_submission, sqlite_submission):
    """
    Guards the drift this package exists to prevent: the drop zone once
    advertised ZIP and JSON while the engine could read neither.
    """
    for path in (DATA, zip_submission, json_submission, sqlite_submission):
        assert classify_input(path) is not None
        assert resolve_dataset(path)


def test_dialog_filter_covers_every_file_format():
    filter_text = dialog_filter()
    for pattern in ("*.zip", "*.json", "*.sqlite"):
        assert pattern in filter_text


def test_unsupported_extension_explains_itself(tmp_path):
    path = tmp_path / "submission.xlsx"
    path.write_text("x")
    assert classify_input(str(path)) is None
    assert ".xlsx" in rejection_reason(str(path))


def test_nonexistent_path_raises_ingestion_error():
    with pytest.raises(IngestionError, match="does not exist"):
        resolve_dataset("/no/such/submission")


def test_accepts_files_now_that_file_formats_exist():
    assert accepts_files() is True
    assert "ZIP" in describe_supported_inputs()


@pytest.mark.parametrize("member", [
    "alerts\x00evil.csv",
    "alerts\nevil.csv",
    "alerts\revil.csv",
    "alerts\x7f.csv",
])
def test_control_characters_in_member_name_are_refused(member):
    """
    A name truncated at a null byte by a C-level filesystem call can
    differ from the string Python validated, so these are refused
    outright rather than being caught incidentally by the extension
    filter.

    Tested against the guard directly because Python's own zipfile
    truncates such a name at the null when writing, so a malicious
    archive of this shape cannot be constructed with it — an external
    writer has no such restriction.
    """
    from analytics.ingestion.safe_archive import _is_unsafe_path

    assert _is_unsafe_path(member) == "control character in member name"


def test_ordinary_member_names_are_allowed():
    """The guard must not reject legitimate submissions."""
    from analytics.ingestion.safe_archive import _is_unsafe_path

    for member in ("alerts.csv", "submission 2026-Q3/alerts.csv",
                    "sub_dir/nested/cases.json", "données.csv"):
        assert _is_unsafe_path(member) is None, member
