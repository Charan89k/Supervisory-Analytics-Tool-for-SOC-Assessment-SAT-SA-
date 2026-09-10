#!/usr/bin/env python3
"""
Headless UI smoke test for the SAT-SA desktop application.

Runs the real MainWindow against the real pipeline under Qt's offscreen
platform plugin. Two properties make it safe to run anywhere:

  * SATSA_NARRATION_BACKEND=mock is forced before any import, so no
    code path can reach Ollama or llama.cpp. No language model is
    loaded, no multi-gigabyte allocation happens, and the run does not
    depend on what is installed on the machine.
  * MainWindow(interactive=False) records modal dialogs instead of
    showing them. A QMessageBox under an offscreen plugin blocks the
    event loop forever, which is what previously turned this test into
    a hang.

Run:  QT_QPA_PLATFORM=offscreen python tests/smoke_ui.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SATSA_NARRATION_BACKEND"] = "mock"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from application.ui import MainWindow  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "synthetic")

_checks = 0


def ok(message):
    global _checks
    _checks += 1
    print(f"  PASS  {message}", flush=True)


def pump(predicate, timeout_ms=120_000):
    """Spin the Qt event loop until `predicate` holds or we time out."""
    loop, elapsed, timer = QEventLoop(), {"t": 0}, QTimer()

    def tick():
        elapsed["t"] += 25
        if predicate() or elapsed["t"] >= timeout_ms:
            timer.stop()
            loop.quit()

    timer.timeout.connect(tick)
    timer.start(25)
    loop.exec()
    return predicate()


def corrupt_copy_of(source):
    """A dataset with a duplicate alert_id — an ERROR-level failure."""
    tmp = tempfile.mkdtemp(prefix="satsa-smoke-")
    for name in os.listdir(source):
        shutil.copy(os.path.join(source, name), tmp)
    alerts = pd.read_csv(os.path.join(tmp, "alerts.csv"))
    pd.concat([alerts, alerts.head(3)]).to_csv(
        os.path.join(tmp, "alerts.csv"), index=False)
    return tmp


def build_zip_submission(destination):
    """The same submission, zipped inside a wrapper folder."""
    path = os.path.join(destination, "submission.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in os.listdir(DATA):
            if name.endswith(".csv"):
                archive.write(os.path.join(DATA, name),
                              f"submission_2026Q3/{name}")
    return path


def build_traversal_archive(destination):
    """A hostile archive that tries to write outside its extraction root."""
    path = os.path.join(destination, "evil.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../../../../tmp/satsa-ui-pwned.csv", "alert_id\nA1\n")
    return path


def main():
    started = time.time()
    app = QApplication(sys.argv)
    window = MainWindow(interactive=False)
    window.show()

    print("\n[1] Window construction")
    assert window.pages.count() == 5
    ok("five pages present")
    assert not window.run_button.isEnabled()
    ok("RUN disabled until a dataset validates")

    print("\n[2] Unsupported input is refused with a reason")
    window.drop_zone.set_dataset(os.path.join(DATA, "alerts.csv"))
    assert window.dataset_path is None, "a bare CSV must not be accepted"
    assert not window.run_button.isEnabled()
    assert "cannot read a .csv file" in window.validation_detail.toPlainText()
    assert window.notifications[-1][0] == "warning"
    ok("single CSV refused, RUN stays disabled, reason shown")

    print("\n[3] Valid dataset validates")
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy), "validation hung"
    assert not window.current_validation_report.has_errors
    assert "PASSED" in window.validation_status.text()
    assert window.run_button.isEnabled()
    ok(f"validation panel reads {window.validation_status.text()!r}")

    print("\n[4] Invalid dataset blocks the run and shows the report")
    corrupt = corrupt_copy_of(DATA)
    try:
        window.drop_zone.set_dataset(corrupt)
        assert pump(lambda: not window.validation_busy)
        assert window.current_validation_report.has_errors
        assert "FAILED" in window.validation_status.text()
        assert "duplicate alert_id" in window.validation_detail.toPlainText()
        assert not window.run_button.isEnabled()
        ok("blocking error surfaced in the panel; RUN disabled")
    finally:
        shutil.rmtree(corrupt, ignore_errors=True)

    print("\n[5] Assessment run")
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy)
    window.ai_mode.setCurrentIndex(window.ai_mode.count() - 1)  # no AI
    window.start_assessment()
    assert pump(lambda: not window.assessment_busy), "assessment hung"
    assert window.current_result is not None
    ok(f"{len(window.findings)} findings, "
       f"{len(window.review_queue)} review-queue items")
    assert window.risk_table.rowCount() == 5
    ok(f"dashboard ranks {window.risk_table.rowCount()} entities")
    assert window.finding_table.rowCount() > 0
    ok(f"findings explorer shows {window.finding_table.rowCount()} rows")
    assert window.review_table.rowCount() > 0
    ok(f"review queue shows {window.review_table.rowCount()} rows")
    assert "report" in window.report_status.text()
    ok(f"reports page reads {window.report_status.text()!r}")

    print("\n[6] Regression: a second assessment can start")
    assert not window.assessment_busy, "busy flag was never cleared"
    window.start_assessment()
    assert pump(lambda: not window.assessment_busy)
    assert not any("already running" in n[2] for n in window.notifications)
    ok("second assessment runs in the same session")

    print("\n[7] New detectors reach the UI")
    types = {f["finding_type"] for f in window.findings}
    for rule in ("ACK_WITHOUT_INVESTIGATION", "REPEATED_ALERT_WITHOUT_REMEDIATION",
                 "MISSING_ALERT_CATEGORY", "MISSING_ESCALATION_RECORDS"):
        assert rule in types, f"{rule} never reached the Findings explorer"
        ok(f"{rule} present")

    print("\n[8] ZIP submission loads through the UI")
    workspace = tempfile.mkdtemp(prefix="satsa-smoke-formats-")
    try:
        window.drop_zone.set_dataset(build_zip_submission(workspace))
        assert pump(lambda: not window.validation_busy), "zip validation hung"
        assert window.dataset_path is not None, "zip was refused"
        assert "PASSED" in window.validation_status.text(), \
            window.validation_status.text()
        assert window.run_button.isEnabled()
        ok("ZIP archive accepted, validated, and cleared for assessment")

        print("\n[9] Hostile archive is refused in the UI, not extracted")
        window.drop_zone.set_dataset(build_traversal_archive(workspace))
        assert pump(lambda: not window.validation_busy)
        assert not window.run_button.isEnabled()
        assert not os.path.exists("/tmp/satsa-ui-pwned.csv"), \
            "path traversal escaped during a UI-driven load"
        detail = window.validation_detail.toPlainText()
        assert "unsafe member path" in detail, detail
        ok("path-traversal archive refused; nothing written outside the root")
        ok(f"supervisor sees: {detail.splitlines()[0][:64]!r}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    print("\n[10] No language model was involved")
    from analytics.narration import resolve_backend_name
    assert resolve_backend_name({}) == "mock"
    ok("narration backend resolved to 'mock' throughout")

    elapsed = time.time() - started
    print(f"\nALL {_checks} UI SMOKE CHECKS PASSED in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
