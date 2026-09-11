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
import subprocess
import sys
import tempfile
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SATSA_NARRATION_BACKEND"] = "mock"

#: Every finding type the engine can emit.
ALL_RULE_TYPES = {
    "MISSED_ESCALATION", "SLOW_TRIAGE", "FAST_CLOSURE", "MISSING_EVIDENCE",
    "REOPENED_CASE", "REPETITIVE_INVESTIGATION", "ANALYST_OVERLOAD",
    "ACK_WITHOUT_INVESTIGATION", "REPEATED_ALERT_WITHOUT_REMEDIATION",
    "TELEMETRY_GAP", "MISSING_ALERT_CATEGORY", "LOW_ACTIVITY_OUTLIER",
    "MISSING_ESCALATION_RECORDS", "MISSING_INVESTIGATIONS",
}
# Never read or write the developer's real settings file.
os.environ["SATSA_CONFIG_DIR"] = tempfile.mkdtemp(prefix="satsa-smoke-config-")
# Assessment history in a throwaway directory, so a smoke run never
# writes into the developer's real history.
os.environ["SATSA_ASSESSMENTS_DIR"] = tempfile.mkdtemp(prefix="satsa-smoke-hist-")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# After the path insert, so the package resolves. The demonstration
# entity count is read from the service rather than repeated here: a
# literal would silently disagree the next time the dataset changes.
from application.services.demo_service import DEMO_ENTITIES  # noqa: E402

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
    assert window.pages.count() == 8
    ok("eight pages present (Dashboard, Assessment, Findings, Queue, "
       "Reports, Benchmarking, History, Settings)")
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
                    window.reports_button, window.benchmark_button):
        assert not button.isEnabled()
    ok("Findings / Review Queue / Reports / Benchmarking gated until "
       "results exist")

    assert not window.findings_placeholder.isHidden()
    assert window.findings_content.isHidden()
    assert "No assessment loaded" in window.findings_placeholder.text()
    assert "Select a dataset" in window.findings_placeholder.text()
    ok("empty state explains what to do rather than showing a blank grid")

    assert "No dataset" in window.status_dataset.text()
    ok(f"status bar: {window.status_phase.text().strip()!r} "
       f"{window.status_dataset.text()!r}")

    print("\n[1c] Demonstration mode: one click, the real pipeline")
    import time as _time
    started_demo = _time.time()
    window.start_demo()
    assert pump(lambda: window.current_result is not None
                and not window.assessment_busy), "demo run hung"
    demo_seconds = _time.time() - started_demo
    ok(f"one click to a loaded assessment in {demo_seconds:.1f}s")

    assert "PASSED" in window.validation_status.text()
    ok("validation was shown, not skipped")

    assert window.demo_active is True
    assert not window.demo_banner.isHidden()
    assert "DEMONSTRATION" in window.demo_banner.text()
    assert "DEMONSTRATION DATA" in window.dashboard_status.text()
    assert "synthetic" in window.session.dataset_label.lower()
    ok("demonstration data labelled in the status bar, dashboard and "
       "dataset name")

    fired = {f["finding_type"] for f in window.findings}

    # LOW_ACTIVITY_OUTLIER compares an entity against its own peer
    # group, and a sample z-score is bounded by group size: a group of
    # 3 tops out at 1.155 and can never reach the configured -1.5. The
    # demonstration dataset has 3-entity sectors, so the rule is
    # structurally unfireable there — computed here rather than
    # hardcoded, so the check re-arms itself if the dataset grows.
    import collections as _collections

    import yaml as _yaml

    from analytics.detection.negative_space import minimum_group_for_zscore

    _cfg = _yaml.safe_load(open(os.path.join(PROJECT_ROOT, "config",
                                              "assessment_rules.yaml")))
    _needed = minimum_group_for_zscore(
        _cfg["negative_space"]["low_activity_zscore_threshold"])
    _sizes = _collections.Counter(e.get("peer_group")
                                   for e in window.current_result["entities"])
    _unfireable = ({"LOW_ACTIVITY_OUTLIER"}
                   if _sizes and max(_sizes.values()) < _needed else set())

    expected = 14 - len(_unfireable)
    assert fired == ALL_RULE_TYPES - _unfireable, sorted(
        (ALL_RULE_TYPES - _unfireable) ^ fired)
    ok(f"{len(fired)} of 14 rules fired on the demonstration dataset"
       + (f"; {', '.join(sorted(_unfireable))} needs a peer group of "
          f"{_needed} and the demo has {max(_sizes.values())}"
          if _unfireable else " (all of them)"))

    assert window.risk_table.rowCount() == DEMO_ENTITIES
    assert window.case_table.rowCount() > 0
    ok(f"{window.risk_table.rowCount()} entities ranked, "
       f"{window.case_table.rowCount()} correlated cases")

    window.show_page(5)
    assert window.group_table.rowCount() >= 3
    ok(f"{window.group_table.rowCount()} comparable peer groups")

    # Selecting a real submission must leave demonstration mode, so the
    # banner can never outlive the data it describes.
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy)
    assert window.demo_active is False
    assert window.demo_banner.isHidden()
    ok("selecting another dataset clears the demonstration banner")

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
    assert window.risk_table.columnCount() == 10
    ok(f"dashboard ranks {window.risk_table.rowCount()} entities "
       f"across {window.risk_table.columnCount()} columns")

    # WHO -> WHY: the page opens on the highest-risk entity and its
    # driver panels follow the selection.
    assert window.selected_dashboard_entity() == "SOC-003"
    assert "SOC-003" in window.driver_note.text()
    ok(f"opens on the highest-risk entity: {window.selected_dashboard_entity()}")

    window.risk_table.selectRow(4)
    assert window.selected_dashboard_entity() == "SOC-001"
    assert "SOC-001" in window.driver_note.text()
    ok("selecting another entity re-drives the risk-driver panel")
    window.risk_table.selectRow(0)

    # Asserted as relationships, not as magic numbers: the dataset is
    # regenerated whenever the generator changes, and a hardcoded 167
    # would fail for reasons unrelated to the UI.
    from application.services import dashboard_service as _ds
    summary = _ds.overview(window.current_result, window.review_queue)
    assert window.kpi_critical.value_label.text() == f"{summary.critical:,}"
    assert window.kpi_high.value_label.text() == f"{summary.high:,}"
    assert window.kpi_anomalies.value_label.text() == f"{summary.anomalies:,}"
    assert summary.critical + summary.high <= summary.total_findings
    ok(f"critical / high / anomaly tiles agree with the assessment "
       f"({summary.critical} / {summary.high} / {summary.anomalies})")

    peer_note = window.peer_note.text()
    assert "peer" in peer_note.lower() and peer_note
    ok(f"peer comparison note: {peer_note[:60]}…")

    assert window.review_preview_table.rowCount() == 10
    assert window.review_preview_table.item(0, 0).text() == "1"
    ok(f"priority review preview shows "
       f"{window.review_preview_table.rowCount()} items, ranked")

    assert "no trend data" in window.trend_note.text().lower()
    ok("single period reports no trend data rather than drawing one")

    for chart in (window.driver_chart, window.severity_chart,
                   window.category_chart):
        series = chart.chart().series()
        assert series, "chart has no series"
        assert series[0].count() == 1, "one bar set per chart keeps bars full-width"
    ok("all three charts rendered with data")
    assert window.finding_table.rowCount() > 0
    ok(f"findings explorer shows {window.finding_table.rowCount()} rows")

    print("\n[5c] Findings explorer: search, filters, sorting")
    from PySide6.QtCore import Qt as _Qt
    total = window.finding_table.rowCount()

    window.finding_severity_filter.setCurrentText("CRITICAL")
    critical = window.finding_table.rowCount()
    assert 0 < critical < total
    ok(f"severity filter: {total} -> {critical}")

    window.finding_category_filter.setCurrentText("Negative Space")
    assert window.finding_table.rowCount() <= critical
    window.clear_finding_filters()
    assert window.finding_table.rowCount() == total
    ok("category filter composes with severity; Clear restores all rows")

    window.finding_search.setText("ROOT-CAUSE-RECURRENCE")
    searched = window.finding_table.rowCount()
    assert 0 < searched < total
    ok(f"search by rule id: {total} -> {searched}")
    window.clear_finding_filters()

    window.finding_soc_filter.setCurrentText("SOC-003")
    assert 0 < window.finding_table.rowCount() < total
    ok(f"entity filter -> {window.finding_table.rowCount()} rows")
    window.clear_finding_filters()

    window.finding_sort.setCurrentIndex(0)
    first = window.finding_table.item(0, 0).data(_Qt.UserRole)
    assert window.finding_severity(first) == "CRITICAL"
    ok("sorting by severity puts CRITICAL first")

    print("\n[5d] Audit chain: Finding -> Rule -> Rationale -> Evidence")
    window.finding_table.selectRow(0)
    detail = window.finding_detail_panel.detail.toPlainText()
    for section in ("FINDING", "RULE \u2014", "RATIONALE (deterministic)",
                     "EVIDENCE (deterministic)"):
        assert section in detail, section
    ok("all four deterministic sections present, in order")

    assert "Configured thresholds used by this rule" in detail
    ok("rule section names the thresholds the run actually used")

    selected = window.finding_detail_panel.current_finding
    assert selected["rationale"] in detail, "rationale was not shown verbatim"
    ok("rationale rendered verbatim, not rephrased")

    print("\n[5e] Source records drill-down")
    window.load_finding_source_records()
    source = window.finding_detail_panel.source_detail.toPlainText()
    assert "SOURCE RECORDS" in source
    assert "grain)" in source
    assert selected.get("alert_id", "") in source or "entity" in source.lower()
    ok(f"source records loaded ({len(source):,} chars) from the submission")

    print("\n[5f] Source unavailable is reported, not shown as empty")
    saved_path = window.session.dataset_path
    window.evidence_service.reset()
    window.session.set_dataset("/no/such/submission", "gone")
    window.load_finding_source_records()
    unavailable = window.finding_detail_panel.source_detail.toPlainText()
    assert "SOURCE RECORDS UNAVAILABLE" in unavailable
    assert "does not mean the finding lacks evidence" in unavailable
    ok("a moved submission reports itself rather than reading as no evidence")
    window.session.set_dataset(saved_path, "synthetic")
    window.evidence_service.reset()
    assert window.case_table.rowCount() > 0
    ok(f"review queue shows {window.case_table.rowCount()} correlated cases")
    assert "artifact" in window.report_status.text()
    ok(f"reports page reads {window.report_status.text()[:70]!r}")

    # Every file the pipeline wrote must be reachable. The page used to
    # carry a hardcoded list of six while the pipeline produced eight.
    produced = {f.name for f in window.current_run.path.iterdir()
                if f.is_file()}
    listed = window.report_list.count()
    assert listed == len(produced), (
        f"{listed} rows for {len(produced)} files: {sorted(produced)}")
    ok(f"all {listed} produced artifact(s) exposed, none hidden")

    print("\n[5b] Results unlock the results pages")
    assert window.session.phase is Phase.LOADED
    for button in (window.findings_button, window.review_button,
                    window.reports_button, window.benchmark_button):
        assert button.isEnabled()
    ok("Findings / Review Queue / Reports / Benchmarking now reachable")

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

    # Every outcome here is legitimate and depends only on what this
    # machine happens to have installed — which is the point: discovery
    # is automatic, and each state names its own remedy rather than
    # collapsing into one unhelpful "unavailable".
    window.ai_settings_saved(AIConfig(enabled=True, backend="ollama",
                                       model="qwen2.5:7b",
                                       availability_timeout=2))
    headline = window.sidebar_ai_status.text()
    assert headline in ("AI NOT INSTALLED", "AI NOT RUNNING",
                         "AI MODEL MISSING", "AI READY"), headline
    status = window.current_ai_status()
    assert status.detail, "every AI state must explain itself"
    assert status.available == (headline == "AI READY")
    if headline != "AI READY":
        assert status.remedy(), f"{headline} offers no next step"
    ok(f"ollama auto-discovery -> {headline!r} (no paths entered, "
       f"no model loaded)")
    ok(f"   detail: {status.detail[:70]}")

    os.environ["SATSA_NARRATION_BACKEND"] = forced_backend

    window.ai_settings_saved(AIConfig(enabled=True, backend="mock"))
    assert window.sidebar_ai_status.text() == "SAMPLE EXPLANATIONS"
    ok("mock -> 'SAMPLE EXPLANATIONS'")

    print("\n[11] Explanations are an explicit action, never automatic")
    window.ai_config.max_explanations = 5
    window.narration_outcome = None
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy)
    window.ai_enabled_box.setChecked(True)
    window.start_assessment()
    assert pump(lambda: not window.assessment_busy), "assessment hung"

    # The critical assertion of this section. Explaining ten findings on
    # a CPU takes minutes and changes nothing about the assessment, so
    # an assessment must finish without starting one.
    assert not window.narration_busy, "narration started on its own"
    assert window.narration_outcome is None, "narration ran unasked"
    assert not any(r.get("narration_source") == "mock"
                    for r in window.review_queue), \
        "findings were narrated without the supervisor asking"
    log = window.assessment_log.toPlainText()
    assert "Explain Top Findings" in log, log[-300:]
    ok("assessment completed WITHOUT generating explanations")
    ok("supervisor is told explanations are available, not made to wait")

    # And now the explicit action.
    window.show_page(3)
    assert window.explain_button.isEnabled(), window.explain_button.toolTip()
    window.explain_button.click()
    assert pump(lambda: not window.narration_busy), "narration hung"

    outcome = window.narration_outcome
    assert outcome is not None, "the Explain action did not run narration"
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

    # The button must also refuse to run when it cannot: a disabled
    # action with a stated reason, not a click that quietly does nothing.
    window.ai_enabled_box.setChecked(False)
    assert not window.explain_button.isEnabled()
    assert "switched off" in window.explain_button.toolTip()
    ok(f"action disabled with a reason: {window.explain_button.toolTip()[:58]}")
    window.ai_enabled_box.setChecked(True)

    print("\n[12] Deterministic result is unchanged by narration")
    assert window.risk_table.rowCount() == 5
    assert len(window.findings) == summary.total_findings
    for record in window.review_queue:
        assert record["severity"] in ("CRITICAL", "HIGH")
        assert record["evidence"] is not None
    ok("entity ranking, findings, severities and evidence all intact")

    print("\n[12b] Review queue groups findings into cases")
    window.show_page(3)
    stats = window.review_stats.text()
    assert "correlated into" in stats
    ok(f"queue stats: {stats}")

    compound_row = None
    for row in range(window.case_table.rowCount()):
        if int(window.case_table.item(row, 3).text()) > 1:
            compound_row = row
            break
    assert compound_row is not None, "no compounding case in the queue"

    window.case_table.selectRow(compound_row)
    case = window.selected_review_case()
    assert case.finding_count > 1
    assert window.review_table.rowCount() == case.finding_count
    ok(f"case {case.case_rank} holds {case.finding_count} findings on one "
       f"alert; all listed below it")

    alerts = {f.get("alert_id") for f in case.findings}
    assert len(alerts) == 1
    ok(f"every finding in the case shares alert {alerts.pop()}")

    why = window.review_detail_panel.detail.toPlainText()
    assert "WHY THIS CASE IS PRIORITISED" in why
    assert "CASE PRIORITY (sum)" in why
    assert f"{case.case_priority:g}" in why
    ok("prioritisation arithmetic shown and matches the case priority")

    total = window.case_table.rowCount()
    window.review_compound_only.setChecked(True)
    assert window.case_table.rowCount() <= total
    window.review_compound_only.setChecked(False)
    window.review_search.setText("INVESTIGATION-ABSENT-001")
    assert 0 < window.case_table.rowCount() < total
    ok(f"queue search by rule id: {total} -> {window.case_table.rowCount()} cases")
    window.review_search.clear()

    window.load_review_source_records()
    queue_source = window.review_detail_panel.source_detail.toPlainText()
    assert "SOURCE RECORDS" in queue_source
    ok(f"queue drill-down reaches source records ({len(queue_source):,} chars)")

    print("\n[13] Explanation surfaces, labelled and structurally separate")
    window.case_table.selectRow(0)
    window.review_table.selectRow(0)
    detail = window.review_detail_panel.detail.toPlainText()
    narration = window.review_detail_panel.ai_text.toPlainText()
    if narration:
        assert "SAMPLE EXPLANATION" in window.review_detail_panel.ai_header.text()
        assert narration not in detail
        ok("queue narration is labelled and absent from the authoritative panel")
    else:
        ok("top queue case has no narration; panel correctly hidden")

    # The invariant this phase turns on: narration text must never be
    # able to reach the authoritative panel. Separate widgets make it
    # structurally impossible, and this asserts it on real data.
    from PySide6.QtCore import Qt as _Qt2
    window.show_page(2)
    window.clear_finding_filters()
    explained_row = None
    for row in range(window.finding_table.rowCount()):
        candidate = window.finding_table.item(row, 0).data(_Qt2.UserRole)
        if window.narration_for(candidate):
            explained_row = row
            break
    assert explained_row is not None, "no explained finding reached the explorer"

    window.finding_table.selectRow(explained_row)
    assert not window.finding_detail_panel.ai_panel.isHidden()
    assert "SAMPLE EXPLANATION" in window.finding_detail_panel.ai_header.text()
    ok(f"explained finding shows the AI panel: "
       f"{window.finding_detail_panel.ai_header.text()!r}")

    narration = window.finding_detail_panel.ai_text.toPlainText()
    authoritative = window.finding_detail_panel.detail.toPlainText()
    assert narration, "AI panel is empty"
    assert narration not in authoritative
    assert "[SAMPLE EXPLANATION]" not in authoritative
    ok("narration text is absent from the authoritative detail panel")

    finding = window.finding_detail_panel.current_finding
    assert finding["rationale"] in authoritative
    assert json.dumps(finding["evidence"], indent=2, default=str) in authoritative
    ok("deterministic rationale and evidence remain visible alongside it")

    # A finding with no explanation must not show the panel at all.
    unexplained = None
    for row in range(window.finding_table.rowCount()):
        candidate = window.finding_table.item(row, 0).data(_Qt2.UserRole)
        if not window.narration_for(candidate):
            unexplained = row
            break
    window.finding_table.selectRow(unexplained)
    assert window.finding_detail_panel.ai_panel.isHidden()
    ok("unexplained finding shows no AI panel at all")

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
    assert "unavailable" in log.lower(), log[-300:]
    ok("assessment completed with an unavailable model; state was reported")

    # Asking anyway must fail safe and say why, not hang or half-run.
    window.show_page(3)
    window.start_narration()
    assert not window.narration_busy
    assert window.narration_status.text().startswith("AI unavailable")
    ok(f"explicit request refused cleanly: "
       f"{window.narration_status.text()[:58]}")
    os.environ["SATSA_NARRATION_BACKEND"] = forced_backend

    print("\n[14a] Capability mapping")
    window.show_page(0)
    assert window.capability_table.rowCount() == 8
    ok("all eight supervisory capability areas reported")

    labels = [window.capability_table.item(r, 0).text()
              for r in range(window.capability_table.rowCount())]
    for expected in ("Threat Detection", "Investigation", "Escalation",
                      "Incident Response", "Security Operations",
                      "Governance and Oversight", "Operational Discipline",
                      "Cyber Resilience"):
        assert expected in labels, expected
    ok("areas match the problem statement's eight")

    # Contributions must still sum to the entity's own risk score, or a
    # finding has been counted against more than one capability.
    from analytics import capabilities as _cs
    soc_id = window.selected_dashboard_entity()
    entity = next(e for e in window.current_result["entities"]
                  if e["soc_id"] == soc_id)
    total = sum(r.contribution
                for r in _cs.assess_entity(entity, window.assessment_config()))
    assert abs(total - entity["supervisory_risk_score"]) < 0.05
    ok(f"capability contributions sum to {soc_id}'s risk score "
       f"({total:.2f}) — no double counting")

    assert "no rule fired" in window.capability_note.text()
    ok("coverage note distinguishes 'no findings' from 'verified'")

    print("\n[14b] Findings explorer: capability area")
    window.show_page(2)
    window.clear_finding_filters()
    total_rows = window.finding_table.rowCount()
    window.finding_capability_filter.setCurrentText("Escalation")
    escalation = window.finding_table.rowCount()
    assert 0 < escalation < total_rows
    ok(f"capability filter: {total_rows} -> {escalation} rows")
    window.clear_finding_filters()

    window.finding_table.selectRow(0)
    detail = window.finding_detail_panel.detail.toPlainText()
    assert "Capability area" in detail
    assert "CAPABILITY AREA —" in detail
    ok("finding detail names its capability area and why")

    print("\n[14c] Peer benchmarking")
    window.show_page(5)
    assert window.group_table.rowCount() > 0
    ok(f"{window.group_table.rowCount()} peer group(s) listed")

    assert window.position_table.rowCount() == 5
    ok(f"{window.position_table.rowCount()} entity positions")

    caveat = window.benchmark_caveat.text()
    assert "indicator for review, not a finding" in caveat or \
        "not a benchmark" in caveat
    ok("benchmarking caveat present")

    # Selecting an entity must say WHAT separates it, not just that it
    # differs — a percentile alone is not something a supervisor can act on.
    window.position_table.selectRow(0)
    assert window.comparison_table.rowCount() > 0
    first = window.comparison_table.item(0, 4).text()
    assert "peer median" in first
    ok(f"top differentiator: "
       f"{window.comparison_table.item(0, 0).text()} — {first[:44]}")

    for row in range(window.comparison_table.rowCount()):
        text = window.comparison_table.item(row, 4).text().lower()
        assert not any(v in text for v in
                        ("failing", "inadequate", "insecure", "deficient"))
    ok("comparison wording states difference, never a verdict")

    print("\n[15b] Multi-period assessment produces real trends")
    periods_dir = os.path.join(tempfile.mkdtemp(prefix="satsa-smoke-periods-"),
                                "submission")
    try:
        subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(DATA), "generator",
                           "generate_dataset.py"),
             "--socs", "3", "--alerts-per-soc", "150", "--periods", "3",
             "--out", periods_dir],
            check=True, capture_output=True)

        window.ai_settings_saved(AIConfig(enabled=False))
        window.drop_zone.set_dataset(periods_dir)
        assert pump(lambda: not window.validation_busy), "validation hung"
        assert window.run_button.isEnabled()
        ok("multi-period submission validated")

        window.start_assessment()
        assert pump(lambda: not window.assessment_busy), "assessment hung"

        report = window.trend_report
        assert report is not None and report.available
        assert len(report.periods) == 3
        ok(f"three periods assessed independently: {', '.join(report.periods)}")

        assert not window.trend_panel.isHidden()
        assert window.trend_table.rowCount() == 3
        ok(f"trend panel shows {window.trend_table.rowCount()} entities")

        # Every series must hold one value per period, drawn from that
        # period's own assessment.
        for soc_id, series in report.risk_score.items():
            assert len(series.values) == 3, soc_id
        ok("each entity has one risk score per period")

        directions = {window.trend_table.item(r, 4).text()
                      for r in range(window.trend_table.rowCount())}
        assert directions <= {"up", "down", "mixed", "stable"}, directions
        ok(f"directions reported: {', '.join(sorted(directions))}")

        chart_series = window.trend_chart.chart().series()
        assert len(chart_series) == 2, "expected a line and its markers"
        ok("trend chart drawn for the selected entity")
    finally:
        shutil.rmtree(os.path.dirname(periods_dir), ignore_errors=True)

    print("\n[15c] A single-period dataset still refuses to invent a trend")
    window.drop_zone.set_dataset(DATA)
    assert pump(lambda: not window.validation_busy)
    window.start_assessment()
    assert pump(lambda: not window.assessment_busy)
    assert window.trend_report is None
    assert window.trend_panel.isHidden()
    assert "no trend data" in window.trend_note.text().lower()
    ok("single period hides the panel and says why")

    print("\n[15d] Assessment history keeps every run")
    window.show_page(6)
    window.refresh_history()
    before = window.history_table.rowCount()
    assert before > 0, "completed assessments were not recorded"
    ok(f"{before} assessment(s) recorded in history")

    run_ids = [r.summary.run_id for r in window.history_runs]
    assert len(set(run_ids)) == len(run_ids)
    paths = [r.path for r in window.history_runs]
    assert len(set(paths)) == len(paths)
    ok("every run has a distinct id and directory")

    for run in window.history_runs:
        assert run.results_path().is_file(), run.run_id
    ok("every stored run still has its results file — none overwritten")

    # Load the oldest run back and confirm the whole application follows it.
    oldest = window.history_runs[-1]
    window.history_table.selectRow(len(window.history_runs) - 1)
    window.load_selected_run()
    assert window.current_run.run_id == oldest.summary.run_id
    assert window.output_dir == oldest.path
    assert window.risk_table.rowCount() > 0
    assert len(window.findings) > 0
    ok(f"loaded {oldest.summary.run_id}; dashboard and findings follow it")

    # Deleting is explicit and removes only what was asked for.
    doomed = window.history_runs[0]
    window.history_table.selectRow(0)
    window.delete_selected_run()
    window.refresh_history()
    assert window.history_table.rowCount() == before - 1
    assert doomed.summary.run_id not in [
        r.summary.run_id for r in window.history_runs]
    assert all(r.results_path().is_file() for r in window.history_runs)
    ok("deleting one run leaves the others intact")

    print("\n[16] Window state persists")
    window.show_page(1)
    window.save_window_state()
    stored = window.settings_service.load_window_state()
    assert stored["page_index"] == 1
    assert stored["geometry"]
    ok(f"geometry and last page ({stored['page_index']}) saved")

    print("\n[15e] Reports follow the loaded assessment")
    window.show_page(6)
    window.refresh_history()
    if window.history_table.rowCount() >= 2:
        window.history_table.selectRow(1)
        older = window.selected_run()
        window.load_selected_run()
        window.show_page(4)
        assert window.current_run.run_id == older.summary.run_id
        assert str(older.path) in window.report_status.text()
        expected = {f.name for f in older.path.iterdir() if f.is_file()}
        assert window.report_list.count() == len(expected)
        ok(f"loading an older run re-points Reports at {older.summary.run_id}")
    else:
        ok("only one assessment stored; history re-pointing not exercised")

    print("\n[16b] Settings: all sections present and wired")
    window.show_page(7)
    page = window.settings_page
    for widget, name in (
        (page.history_path_edit, "General: history location"),
        (page.reopen_last_page_box, "General: reopen last page"),
        (page.strict_validation_box, "Data: strict validation"),
        (page.archive_size_spin, "Data: archive size limit"),
        (page.archive_members_spin, "Data: archive member limit"),
        (page.csv_exports_box, "Reports: CSV exports"),
        (page.pdf_report_box, "Reports: PDF summary"),
    ):
        assert widget is not None, name
    ok("General, Data and Reports controls present")

    assert len(page.system_rows) == 8
    page.set_system_info(window.system_information())
    assert APP_VERSION in page.system_rows["version"].text()
    assert "Not used" in page.system_rows["network"].text()
    ok("System section reports version and offline status")

    # A saved setting must reach the behaviour it names.
    from application.services.app_settings import AppSettings as _AppSettings
    import tempfile as _tempfile
    relocated = _tempfile.mkdtemp(prefix="satsa-smoke-relocated-")
    page.load_app_settings(_AppSettings(
        history_directory=relocated, generate_pdf_report=False))
    saved = page.save()
    assert window.app_settings.history_directory == relocated
    assert str(window.history.root) == relocated
    ok("changing the history location moves the store immediately")

    assert window.settings_service.load_app_settings().history_directory \
        == relocated
    ok("settings persisted to disk")

    # Restore defaults so the rest of the run is unaffected.
    page.load_app_settings(_AppSettings())
    page.save()
    assert window.app_settings.history_directory == ""
    ok("reset restores the default history location")

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
