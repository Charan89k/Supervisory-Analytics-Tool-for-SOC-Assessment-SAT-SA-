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
# Never read or write the developer's real settings file.
os.environ["SATSA_CONFIG_DIR"] = tempfile.mkdtemp(prefix="satsa-smoke-config-")

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
    assert window.pages.count() == 6
    ok("six pages present (Dashboard, Assessment, Findings, Queue, Reports, Settings)")
    assert not window.run_button.isEnabled()
    ok("RUN disabled until a dataset validates")

    from application.session import Phase
    from application.version import APP_NAME, APP_VERSION

    assert APP_VERSION in window.windowTitle()
    assert not window.windowIcon().isNull()
    ok(f"identity: {APP_NAME} v{APP_VERSION}, window icon set")

    menus = [a.text() for a in window.menuBar().actions()]
    assert menus == ["&Assessment", "&View", "&Help"], menus
    ok(f"menu bar: {', '.join(m.replace('&', '') for m in menus)}")

    print("\n[1b] Launch state: no assessment")
    assert window.session.phase is Phase.NO_DATASET
    for button in (window.findings_button, window.review_button,
                    window.reports_button):
        assert not button.isEnabled()
    ok("Findings / Review Queue / Reports gated until results exist")

    assert not window.findings_placeholder.isHidden()
    assert window.findings_content.isHidden()
    assert "No assessment loaded" in window.findings_placeholder.text()
    assert "Select a dataset" in window.findings_placeholder.text()
    ok("empty state explains what to do rather than showing a blank grid")

    assert "No dataset" in window.status_dataset.text()
    ok(f"status bar: {window.status_phase.text().strip()!r} "
       f"{window.status_dataset.text()!r}")

    print("\n[2] Unsupported input is refused with a reason")
    window.drop_zone.set_dataset(os.path.join(DATA, "alerts.csv"))
    assert window.dataset_path is None, "a bare CSV must not be accepted"
    assert not window.run_button.isEnabled()
    assert "cannot read a .csv file" in window.validation_detail.toPlainText()
    assert window.notifications[-1][0] == "warning"
    ok("single CSV refused, RUN stays disabled, reason shown")

    print("\n[3] Valid dataset validates")
    # phase should move NO_DATASET -> VALIDATING -> READY
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy), "validation hung"
    assert not window.current_validation_report.has_errors
    assert "PASSED" in window.validation_status.text()
    assert window.run_button.isEnabled()
    assert window.session.phase is Phase.READY
    ok(f"validation panel reads {window.validation_status.text()!r}")
    ok(f"phase -> {window.session.phase.value}")

    print("\n[4] Invalid dataset blocks the run and shows the report")
    corrupt = corrupt_copy_of(DATA)
    try:
        window.drop_zone.set_dataset(corrupt)
        assert pump(lambda: not window.validation_busy)
        assert window.current_validation_report.has_errors
        assert "FAILED" in window.validation_status.text()
        assert "duplicate alert_id" in window.validation_detail.toPlainText()
        assert not window.run_button.isEnabled()
        assert window.session.phase is Phase.INVALID
        ok("blocking error surfaced in the panel; RUN disabled")
        ok(f"phase -> {window.session.phase.value}")
    finally:
        shutil.rmtree(corrupt, ignore_errors=True)

    print("\n[5] Assessment run")
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy)
    window.ai_enabled_box.setChecked(False)   # deterministic-only run
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

    print("\n[5b] Results unlock the results pages")
    assert window.session.phase is Phase.LOADED
    for button in (window.findings_button, window.review_button,
                    window.reports_button):
        assert button.isEnabled()
    ok("Findings / Review Queue / Reports now reachable")

    window.show_page(2)
    assert not window.findings_content.isHidden()
    assert window.findings_placeholder.isHidden()
    ok("placeholder replaced by content")

    assert window.session.last_assessment_at
    assert "Last assessment" in window.status_dataset.text()
    ok(f"status bar: {window.status_dataset.text()!r}")

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

    print("\n[10] AI status states")
    from application.services.ai_config import AIConfig

    # The mock override is lifted for this section ONLY, so the UI is
    # exercised against real backend selection. This loads no model:
    # LlamaCppBackend.probe() imports the library and stats a file, and
    # OllamaBackend.probe() is one HTTP GET to loopback against a 2s
    # timeout. Neither reads model weights. It is restored afterwards so
    # every explanation generated below still comes from the mock.
    forced_backend = os.environ.pop("SATSA_NARRATION_BACKEND")

    window.ai_settings_saved(AIConfig(enabled=False))
    assert window.sidebar_ai_status.text() == "AI DISABLED"
    ok("disabled  -> 'AI DISABLED'")

    window.ai_settings_saved(AIConfig(enabled=True, backend="llamacpp",
                                       model_path=""))
    assert window.sidebar_ai_status.text() == "AI NOT CONFIGURED"
    ok("llama.cpp with no model file -> 'AI NOT CONFIGURED'")

    window.ai_settings_saved(AIConfig(enabled=True, backend="ollama",
                                       model="qwen2.5:7b",
                                       availability_timeout=2))
    assert window.sidebar_ai_status.text() in (
        "AI UNAVAILABLE", "AI AVAILABLE"), window.sidebar_ai_status.text()
    ok(f"ollama probe -> {window.sidebar_ai_status.text()!r} (no model loaded)")

    os.environ["SATSA_NARRATION_BACKEND"] = forced_backend

    window.ai_settings_saved(AIConfig(enabled=True, backend="mock"))
    assert window.sidebar_ai_status.text() == "SAMPLE EXPLANATIONS"
    ok("mock -> 'SAMPLE EXPLANATIONS'")

    print("\n[11] Explanations run after an assessment, bounded by scope")
    window.ai_config.max_explanations = 5
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy)
    window.ai_enabled_box.setChecked(True)
    window.start_assessment()
    assert pump(lambda: not window.assessment_busy), "assessment hung"
    assert pump(lambda: not window.narration_busy), "narration hung"

    outcome = window.narration_outcome
    assert outcome is not None, "narration never ran"
    assert outcome.explained == 5, outcome.explained
    ok(f"{outcome.explained} of {outcome.considered} queue items explained "
       f"(from {len(window.findings)} findings)")

    explained = [r for r in window.review_queue
                 if r.get("narration_source") == "mock"]
    assert len(explained) == 5
    assert all(r["narration_is_mock"] for r in explained)
    ok("explanations tagged is_mock and stored separately from evidence")

    untouched = [r for r in window.review_queue
                 if r.get("narration_source") == "rule"]
    assert len(untouched) == len(window.review_queue) - 5
    ok(f"{len(untouched)} out-of-scope items kept their rule rationale")

    print("\n[12] Deterministic result is unchanged by narration")
    assert window.risk_table.rowCount() == 5
    assert len(window.findings) == 1479
    for record in window.review_queue:
        assert record["severity"] in ("CRITICAL", "HIGH")
        assert record["evidence"] is not None
    ok("entity ranking, findings, severities and evidence all intact")

    print("\n[13] Explanation surfaces in the detail panel, labelled")
    window.review_table.selectRow(0)
    detail = window.review_detail.toPlainText()
    assert "SAMPLE EXPLANATION (NO MODEL)" in detail, detail[:200]
    assert "no language model" in detail.lower()
    assert "authoritative record" in detail
    ok("detail panel shows the sample label and the authoritative-record note")

    print("\n[14] Cancellation")
    from application.narration_worker import NarrationWorker
    from application.services.ai_config import AIConfig as _AIConfig
    queue = [dict(r) for r in window.review_queue]
    for record in queue:
        record.pop("narration_source", None)
        record.pop("narrated_explanation", None)
    worker = NarrationWorker(queue, _AIConfig(enabled=True, backend="mock",
                                               max_explanations=50))
    seen = []
    worker.progress.connect(lambda d, t: (seen.append(d),
                                           worker.cancel() if d >= 3 else None))
    worker.run()
    assert worker._cancelled is True
    explained_after_cancel = sum(
        1 for r in queue if r.get("narration_source") == "mock")
    assert 0 < explained_after_cancel < 50, explained_after_cancel
    ok(f"cancelled after {explained_after_cancel} explanations; "
       f"partial results kept")

    print("\n[15] AI failure never fails the assessment")
    forced_backend = os.environ.pop("SATSA_NARRATION_BACKEND")
    window.ai_settings_saved(AIConfig(enabled=True, backend="llamacpp",
                                       model_path="/nonexistent/model.gguf"))
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy)
    window.start_assessment()
    assert pump(lambda: not window.assessment_busy)
    assert window.current_result is not None
    assert window.risk_table.rowCount() == 5
    assert not window.narration_busy
    log = window.assessment_log.toPlainText()
    assert "skipped" in log.lower(), log[-300:]
    ok("assessment completed with an unavailable model; skip was reported")
    os.environ["SATSA_NARRATION_BACKEND"] = forced_backend

    print("\n[16] Window state persists")
    window.show_page(1)
    window.save_window_state()
    stored = window.settings_service.load_window_state()
    assert stored["page_index"] == 1
    assert stored["geometry"]
    ok(f"geometry and last page ({stored['page_index']}) saved")

    print("\n[17] About and System Information")
    before = len(window.notifications)
    window.show_about()
    window.show_system_information()
    assert len(window.notifications) == before + 2
    about = window.notifications[-2][2]
    system = window.notifications[-1][2]
    assert APP_VERSION in about
    assert "does not replace" in about.lower()
    assert "no external" in system.lower()
    assert str(window.settings_service.path) in system
    ok("About states the tool supports rather than replaces judgement")
    ok("System Information reports offline status and the settings path")

    print("\n[18] No language model was involved")
    from analytics.narration import resolve_backend_name
    assert resolve_backend_name({}) == "mock"
    ok("narration backend resolved to 'mock' throughout")

    elapsed = time.time() - started
    print(f"\nALL {_checks} UI SMOKE CHECKS PASSED in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
