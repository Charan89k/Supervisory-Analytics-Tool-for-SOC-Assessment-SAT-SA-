from pathlib import Path
import json

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from application.widgets.drop_zone import DropZone
from application.worker import AssessmentWorker, ValidationWorker
from application.services.ai_config import create_ai_config


class MainWindow(QMainWindow):
    """
    The SAT-SA supervisory workstation.

    `interactive=False` routes every modal dialog into `self.notifications`
    instead of showing it. Without that seam the window cannot be driven
    by an automated test at all: a QMessageBox blocks the event loop until
    somebody clicks it, and under an offscreen platform plugin nobody ever
    does, so the run hangs rather than fails. Tests still assert on what
    WOULD have been shown, because the notifications are recorded.
    """

    def __init__(self, interactive: bool = True):
        super().__init__()

        self.interactive = interactive
        self.notifications = []

        self.setWindowTitle("SAT-SA — Supervisory Analytics Tool")
        self.resize(1450, 900)

        self.selected_dataset = None
        self.dataset_path = None

        self.current_result = None
        self.current_ai_config = None

        # Qt object references are HELD, never nulled from inside a
        # slot connected to their own finished signal. Dropping the last
        # Python reference to a QThread while Qt is still emitting from
        # it lets PySide6 collect the wrapper mid-emission, which
        # segfaults in SignalManager::callPythonMetaMethod. Busy state
        # is tracked separately so nothing has to touch lifetimes.
        self.thread = None
        self.worker = None
        self.assessment_busy = False

        self.validation_thread = None
        self.validation_worker = None
        self.validation_busy = False
        self.current_validation_report = None

        self.findings = []
        self.review_queue = []

        self.project_root = Path(__file__).resolve().parents[1]
        self.output_dir = self.project_root / "outputs" / "desktop_assessment"

        self.build_ui()
        self.apply_styles()

    # ============================================================
    # UI
    # ============================================================

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --------------------------------------------------------
        # SIDEBAR
        # --------------------------------------------------------

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 24, 18, 20)
        sidebar_layout.setSpacing(8)

        title = QLabel("SAT-SA")
        title.setObjectName("sidebar_title")

        subtitle = QLabel("SOC Supervisory Analytics")
        subtitle.setObjectName("sidebar_subtitle")
        subtitle.setWordWrap(True)

        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(25)

        self.dashboard_button = self.make_nav_button("Dashboard")
        self.new_assessment_button = self.make_nav_button("New Assessment")
        self.findings_button = self.make_nav_button("Findings")
        self.review_button = self.make_nav_button("Review Queue")
        self.reports_button = self.make_nav_button("Reports")

        sidebar_layout.addWidget(self.dashboard_button)
        sidebar_layout.addWidget(self.new_assessment_button)
        sidebar_layout.addWidget(self.findings_button)
        sidebar_layout.addWidget(self.review_button)
        sidebar_layout.addWidget(self.reports_button)

        sidebar_layout.addStretch()

        status_label = QLabel("OFFLINE / AIR-GAPPED")
        status_label.setObjectName("offline_status")
        status_label.setAlignment(Qt.AlignCenter)

        sidebar_layout.addWidget(status_label)

        # --------------------------------------------------------
        # MAIN STACK
        # --------------------------------------------------------

        self.pages = QStackedWidget()

        self.dashboard_page = self.build_dashboard_page()
        self.assessment_page = self.build_assessment_page()
        self.findings_page = self.build_findings_page()
        self.review_page = self.build_review_page()
        self.reports_page = self.build_reports_page()

        self.pages.addWidget(self.dashboard_page)
        self.pages.addWidget(self.assessment_page)
        self.pages.addWidget(self.findings_page)
        self.pages.addWidget(self.review_page)
        self.pages.addWidget(self.reports_page)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.pages)

        # Navigation
        self.dashboard_button.clicked.connect(
            lambda: self.show_page(0)
        )
        self.new_assessment_button.clicked.connect(
            lambda: self.show_page(1)
        )
        self.findings_button.clicked.connect(
            lambda: self.show_page(2)
        )
        self.review_button.clicked.connect(
            lambda: self.show_page(3)
        )
        self.reports_button.clicked.connect(
            lambda: self.show_page(4)
        )

        self.show_page(0)

    # ============================================================
    # NOTIFICATIONS
    # ============================================================

    def notify(self, level, title, message):
        """
        Single exit point for every modal message the window raises.
        Records the notification either way, so behaviour under test is
        the same code path the supervisor exercises — not a branch that
        only tests take.
        """
        self.notifications.append((level, title, message))

        if not self.interactive:
            return

        {
            "info": QMessageBox.information,
            "warning": QMessageBox.warning,
            "critical": QMessageBox.critical,
        }.get(level, QMessageBox.information)(self, title, message)

    def make_nav_button(self, text):
        button = QPushButton(text)
        button.setObjectName("nav_button")
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(42)
        return button

    def show_page(self, index):
        self.pages.setCurrentIndex(index)

        buttons = [
            self.dashboard_button,
            self.new_assessment_button,
            self.findings_button,
            self.review_button,
            self.reports_button,
        ]

        for i, button in enumerate(buttons):
            button.setProperty(
                "active",
                i == index,
            )
            button.style().unpolish(button)
            button.style().polish(button)

    # ============================================================
    # DASHBOARD
    # ============================================================

    def build_dashboard_page(self):
        page = QWidget()

        layout = QVBoxLayout(page)
        layout.setContentsMargins(35, 30, 35, 30)
        layout.setSpacing(20)

        header = QLabel("Supervisory Dashboard")
        header.setObjectName("page_title")

        description = QLabel(
            "Entity-level cyber resilience assessment and supervisory risk overview."
        )
        description.setObjectName("page_description")

        layout.addWidget(header)
        layout.addWidget(description)

        self.dashboard_status = QLabel(
            "No assessment loaded."
        )
        self.dashboard_status.setObjectName("status_label")

        layout.addWidget(self.dashboard_status)

        # KPI row
        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(15)

        self.kpi_socs = self.make_kpi_card(
            "SOC Entities",
            "0",
        )

        self.kpi_findings = self.make_kpi_card(
            "Total Findings",
            "0",
        )

        self.kpi_execution = self.make_kpi_card(
            "Execution Gaps",
            "0",
        )

        self.kpi_negative = self.make_kpi_card(
            "Negative Space",
            "0",
        )

        self.kpi_review = self.make_kpi_card(
            "Review Queue",
            "0",
        )

        kpi_layout.addWidget(self.kpi_socs)
        kpi_layout.addWidget(self.kpi_findings)
        kpi_layout.addWidget(self.kpi_execution)
        kpi_layout.addWidget(self.kpi_negative)
        kpi_layout.addWidget(self.kpi_review)

        layout.addLayout(kpi_layout)

        # Risk table
        risk_title = QLabel("SOC Risk Ranking")
        risk_title.setObjectName("section_title")

        layout.addWidget(risk_title)

        self.risk_table = QTableWidget()
        self.risk_table.setColumnCount(6)
        self.risk_table.setHorizontalHeaderLabels(
            [
                "Rank",
                "SOC",
                "Organization",
                "Peer Group",
                "Risk Score",
                "Execution Gaps",
            ]
        )

        self.risk_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.risk_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )
        self.risk_table.horizontalHeader().setStretchLastSection(
            True
        )

        layout.addWidget(self.risk_table)

        return page

    def make_kpi_card(self, title, value):
        card = QFrame()
        card.setObjectName("kpi_card")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 15, 20, 15)

        title_label = QLabel(title)
        title_label.setObjectName("kpi_title")

        value_label = QLabel(value)
        value_label.setObjectName("kpi_value")

        layout.addWidget(title_label)
        layout.addWidget(value_label)

        card.value_label = value_label

        return card

    # ============================================================
    # NEW ASSESSMENT
    # ============================================================

    def build_assessment_page(self):
        page = QWidget()

        layout = QVBoxLayout(page)
        layout.setContentsMargins(35, 30, 35, 30)
        layout.setSpacing(18)

        title = QLabel("New Assessment")
        title.setObjectName("page_title")

        description = QLabel(
            "Load a structured SOC dataset and run the offline SAT-SA assessment."
        )
        description.setObjectName("page_description")

        layout.addWidget(title)
        layout.addWidget(description)

        self.drop_zone = DropZone()

        self.drop_zone.path_selected.connect(
            self.dataset_selected
        )

        layout.addWidget(self.drop_zone)

        self.drop_zone.path_rejected.connect(
            self.dataset_rejected
        )

        # ---- Validation panel -------------------------------------
        # The supervisory workflow is VALIDATE -> SHOW RESULT -> RUN.
        # Validation used to happen inside the pipeline, print to a
        # stdout the GUI never showed, and surface as an opaque
        # "pipeline returned no result". The supervisor now sees what
        # was checked and what failed, before committing to a run.
        validation_title = QLabel("Data Validation")
        validation_title.setObjectName("section_title")
        layout.addWidget(validation_title)

        self.validation_status = QLabel(
            "Select a dataset to validate."
        )
        self.validation_status.setObjectName("validation_status")
        self.validation_status.setWordWrap(True)
        layout.addWidget(self.validation_status)

        self.validation_detail = QTextEdit()
        self.validation_detail.setReadOnly(True)
        self.validation_detail.setObjectName("validation_detail")
        self.validation_detail.setMaximumHeight(130)
        self.validation_detail.hide()
        layout.addWidget(self.validation_detail)

        ai_title = QLabel("Explanation Engine")
        ai_title.setObjectName("section_title")

        layout.addWidget(ai_title)

        self.ai_mode = QComboBox()
        self.ai_mode.addItems(
            [
                "Fast — Qwen 2.5 3B",
                "Balanced — Qwen 2.5 7B",
                "Deep — Qwen 2.5 14B",
                "Deterministic Only — No AI",
            ]
        )

        layout.addWidget(self.ai_mode)

        self.run_button = QPushButton(
            "RUN ASSESSMENT"
        )
        self.run_button.setObjectName("primary_button")
        self.run_button.setMinimumHeight(50)
        self.run_button.setCursor(
            Qt.PointingHandCursor
        )

        self.run_button.clicked.connect(
            self.start_assessment
        )
        self.run_button.setEnabled(False)

        layout.addWidget(self.run_button)

        self.progress_label = QLabel(
            "Ready."
        )
        self.progress_label.setObjectName(
            "progress_label"
        )

        layout.addWidget(self.progress_label)

        self.assessment_log = QTextEdit()
        self.assessment_log.setReadOnly(True)
        self.assessment_log.setObjectName(
            "assessment_log"
        )

        layout.addWidget(self.assessment_log)

        return page

    def dataset_selected(self, path):
        self.selected_dataset = path
        self.dataset_path = path

        self.progress_label.setText(
            f"Dataset selected: {path}"
        )

        self.start_validation(path)

    def dataset_rejected(self, reason):
        """The drop zone was given something the engine cannot read."""
        self.dataset_path = None
        self.run_button.setEnabled(False)

        self.set_validation_state(
            "error",
            "Unsupported dataset input.",
            reason,
        )

        self.notify(
            "warning",
            "Unsupported Dataset",
            reason,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def set_validation_state(self, state, headline, detail=""):
        self.validation_status.setProperty("state", state)
        self.validation_status.setText(headline)
        self.validation_status.style().unpolish(self.validation_status)
        self.validation_status.style().polish(self.validation_status)

        if detail:
            self.validation_detail.setPlainText(detail)
            self.validation_detail.show()
        else:
            self.validation_detail.clear()
            self.validation_detail.hide()

    def start_validation(self, path):
        if self.validation_busy:
            return

        self.validation_busy = True
        self.run_button.setEnabled(False)
        self.set_validation_state("running", "Validating dataset...")

        self.validation_thread = QThread()
        self.validation_worker = ValidationWorker(path)
        self.validation_worker.moveToThread(self.validation_thread)

        self.validation_thread.started.connect(self.validation_worker.run)
        self.validation_worker.finished.connect(self.validation_finished)
        self.validation_worker.failed.connect(self.validation_error)

        self.validation_worker.finished.connect(self.validation_thread.quit)
        self.validation_worker.failed.connect(self.validation_thread.quit)
        self.validation_thread.finished.connect(self.validation_worker.deleteLater)
        self.validation_thread.finished.connect(self.validation_thread.deleteLater)

        self.validation_thread.start()

    def validation_finished(self, report):
        self.validation_busy = False
        self.current_validation_report = report

        errors = report.errors
        warnings = report.warnings

        if errors:
            self.set_validation_state(
                "error",
                f"Validation FAILED — {len(errors)} blocking error(s). "
                "The assessment cannot run until these are corrected.",
                self.format_validation_issues(report),
            )
            self.run_button.setEnabled(False)
            return

        if warnings:
            self.set_validation_state(
                "warning",
                f"Validation PASSED with {len(warnings)} warning(s). "
                "The assessment can run; review the notes below.",
                self.format_validation_issues(report),
            )
        else:
            self.set_validation_state(
                "ok",
                "Validation PASSED — no structural issues found.",
            )

        self.run_button.setEnabled(True)

    def validation_error(self, message):
        self.validation_busy = False
        self.run_button.setEnabled(False)
        self.set_validation_state(
            "error",
            "Dataset could not be read.",
            message,
        )

    @staticmethod
    def format_validation_issues(report) -> str:
        lines = []
        for issue in report.issues:
            rows = f"  ({issue.row_count} rows)" if issue.row_count else ""
            lines.append(
                f"[{issue.severity}] {issue.table}: {issue.message}{rows}"
            )
        return "\n".join(lines)

    def start_assessment(self):
        if not self.dataset_path:
            self.notify(
                "warning",
                "No Dataset",
                "Please select or drop a dataset first.",
            )
            return

        if self.assessment_busy:
            self.notify(
                "info",
                "Assessment Running",
                "An assessment is already running.",
            )
            return

        mode_index = self.ai_mode.currentIndex()

        if mode_index == 0:
            ai_config = create_ai_config(
                mode="fast",
                enabled=True,
            )
        elif mode_index == 1:
            ai_config = create_ai_config(
                mode="balanced",
                enabled=True,
            )
        elif mode_index == 2:
            ai_config = create_ai_config(
                mode="deep",
                enabled=True,
            )
        else:
            ai_config = create_ai_config(
                mode="balanced",
                enabled=False,
            )

        self.current_ai_config = ai_config

        self.assessment_busy = True
        self.assessment_log.clear()

        self.run_button.setEnabled(False)

        self.progress_label.setText(
            "Running assessment..."
        )

        self.thread = QThread()
        self.worker = AssessmentWorker(
            self.dataset_path,
            ai_config=ai_config,
        )

        self.worker.moveToThread(
            self.thread
        )

        self.thread.started.connect(
            self.worker.run
        )

        self.worker.progress.connect(
            self.assessment_progress
        )

        self.worker.finished.connect(
            self.assessment_finished
        )

        self.worker.failed.connect(
            self.assessment_failed
        )

        self.worker.validation_failed.connect(
            self.assessment_validation_failed
        )

        self.worker.finished.connect(
            self.thread.quit
        )

        self.worker.failed.connect(
            self.thread.quit
        )

        self.worker.validation_failed.connect(
            self.thread.quit
        )

        self.thread.finished.connect(
            self.worker.deleteLater
        )

        self.thread.finished.connect(
            self.thread.deleteLater
        )

        self.thread.start()

    def assessment_progress(self, message):
        self.progress_label.setText(
            message
        )

        self.assessment_log.append(
            f"✓ {message}"
        )

    def assessment_finished(self, result):
        self.assessment_busy = False
        self.current_result, self.current_ai_config = result

        self.progress_label.setText(
            "Assessment completed."
        )

        self.assessment_log.append(
            ""
        )
        self.assessment_log.append(
            "✓ Assessment completed successfully."
        )

        self.run_button.setEnabled(True)

        # Process all generated data
        self.prepare_result_data()

        # Refresh every application page
        self.refresh_dashboard()
        self.refresh_findings()
        self.refresh_review_queue()
        self.refresh_reports()

        self.notify(
            "info",
            "Assessment Complete",
            "SAT-SA assessment completed successfully.\n\n"
            "Dashboard, Findings, Review Queue and Reports "
            "have been updated.",
        )

        self.show_page(0)

        # self.thread / self.worker are deliberately left set. Qt owns
        # their teardown via deleteLater; assessment_busy is what gates
        # the next run.

    def assessment_validation_failed(self, report):
        """
        The dataset changed between validation and run, or the run was
        started against a dataset that never passed. Show the report,
        not a stack trace.
        """
        self.assessment_busy = False
        self.current_validation_report = report

        self.progress_label.setText("Assessment stopped — dataset validation failed.")
        self.assessment_log.append("")
        self.assessment_log.append("\u2717 Dataset validation failed. Assessment not run.")
        for issue in report.issues:
            rows = f" ({issue.row_count} rows)" if issue.row_count else ""
            self.assessment_log.append(
                f"    [{issue.severity}] {issue.table}: {issue.message}{rows}"
            )

        self.run_button.setEnabled(False)
        self.set_validation_state(
            "error",
            f"Validation FAILED — {len(report.errors)} blocking error(s). "
            "The assessment was not run.",
            self.format_validation_issues(report),
        )

        self.show_page(1)

        self.notify(
            "critical",
            "Dataset Validation Failed",
            "The assessment did not run because the dataset failed "
            "validation.\n\n" + self.format_validation_issues(report),
        )

    def assessment_failed(self, message):
        self.assessment_busy = False
        self.progress_label.setText(
            "Assessment failed."
        )

        self.assessment_log.append(
            f"✗ ERROR: {message}"
        )

        self.run_button.setEnabled(True)

        self.notify(
            "critical",
            "Assessment Failed",
            message,
        )

    # ============================================================
    # RESULT PROCESSING
    # ============================================================

    def prepare_result_data(self):
        if not self.current_result:
            self.findings = []
            self.review_queue = []
            return

        result = self.current_result

        self.findings = []

        for entity in result.get(
            "entities",
            [],
        ):
            # Execution gap findings
            for finding in entity.get(
                "execution_gap_findings",
                [],
            ):
                item = dict(finding)

                item["_category"] = (
                    "Execution Gap"
                )

                self.findings.append(item)

            # Negative-space findings
            for finding in entity.get(
                "negative_space_findings",
                [],
            ):
                item = dict(finding)

                item["_category"] = (
                    "Negative Space"
                )

                self.findings.append(item)

        self.review_queue = list(
            result.get(
                "review_queue",
                [],
            )
        )

    # ============================================================
    # FINDINGS
    # ============================================================

    def build_findings_page(self):
        page = QWidget()

        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            30,
            25,
            30,
            25,
        )

        title = QLabel("Findings")
        title.setObjectName("page_title")

        layout.addWidget(title)

        description = QLabel(
            "Evidence-backed execution gaps and negative-space findings."
        )
        description.setObjectName(
            "page_description"
        )

        layout.addWidget(description)

        # Filters
        filters = QHBoxLayout()

        self.finding_type_filter = QComboBox()
        self.finding_type_filter.addItems(
            [
                "All Findings",
                "Execution Gap",
                "Negative Space",
            ]
        )

        self.finding_soc_filter = QComboBox()
        self.finding_soc_filter.addItem(
            "All SOCs"
        )

        self.finding_severity_filter = QComboBox()
        self.finding_severity_filter.addItems(
            [
                "All Severities",
                "CRITICAL",
                "HIGH",
                "MEDIUM",
                "LOW",
            ]
        )

        filters.addWidget(
            self.finding_type_filter
        )
        filters.addWidget(
            self.finding_soc_filter
        )
        filters.addWidget(
            self.finding_severity_filter
        )
        filters.addStretch()

        layout.addLayout(filters)

        self.finding_type_filter.currentIndexChanged.connect(
            self.refresh_findings
        )
        self.finding_soc_filter.currentIndexChanged.connect(
            self.refresh_findings
        )
        self.finding_severity_filter.currentIndexChanged.connect(
            self.refresh_findings
        )

        splitter = QSplitter(Qt.Horizontal)

        # Findings list
        self.finding_table = QTableWidget()
        self.finding_table.setColumnCount(7)

        self.finding_table.setHorizontalHeaderLabels(
            [
                "Severity",
                "Type",
                "SOC",
                "Alert",
                "Case",
                "Rule",
                "Analyst",
            ]
        )

        self.finding_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )

        self.finding_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )

        self.finding_table.itemSelectionChanged.connect(
            self.finding_selected
        )

        # Detail panel
        detail = QFrame()
        detail.setObjectName(
            "detail_panel"
        )

        detail_layout = QVBoxLayout(detail)

        self.finding_detail_title = QLabel(
            "Select a finding"
        )
        self.finding_detail_title.setObjectName(
            "detail_title"
        )

        detail_layout.addWidget(
            self.finding_detail_title
        )

        self.finding_detail = QTextEdit()
        self.finding_detail.setReadOnly(
            True
        )

        detail_layout.addWidget(
            self.finding_detail
        )

        splitter.addWidget(
            self.finding_table
        )

        splitter.addWidget(detail)

        splitter.setSizes(
            [
                750,
                500,
            ]
        )

        layout.addWidget(splitter)

        return page

    def refresh_findings(self):
        if not hasattr(
            self,
            "finding_table",
        ):
            return

        selected_type = (
            self.finding_type_filter.currentText()
        )

        selected_soc = (
            self.finding_soc_filter.currentText()
        )

        selected_severity = (
            self.finding_severity_filter.currentText()
        )

        # Refresh SOC filter
        current_soc = selected_soc

        socs = sorted(
            {
                f.get("soc_id")
                for f in self.findings
                if f.get("soc_id")
            }
        )

        self.finding_soc_filter.blockSignals(
            True
        )

        self.finding_soc_filter.clear()
        self.finding_soc_filter.addItem(
            "All SOCs"
        )

        for soc in socs:
            self.finding_soc_filter.addItem(
                soc
            )

        index = self.finding_soc_filter.findText(
            current_soc
        )

        if index >= 0:
            self.finding_soc_filter.setCurrentIndex(
                index
            )

        self.finding_soc_filter.blockSignals(
            False
        )

        filtered = []

        for finding in self.findings:
            if (
                selected_type != "All Findings"
                and finding.get("_category")
                != selected_type
            ):
                continue

            if (
                selected_soc != "All SOCs"
                and finding.get("soc_id")
                != selected_soc
            ):
                continue

            severity = str(
                finding.get(
                    "severity",
                    "",
                )
            ).upper()

            if (
                selected_severity
                != "All Severities"
                and severity
                != selected_severity
            ):
                continue

            filtered.append(
                finding
            )

        self.finding_table.setRowCount(
            len(filtered)
        )

        for row, finding in enumerate(
            filtered
        ):
            values = [
                finding.get(
                    "severity",
                    "N/A",
                ),
                finding.get(
                    "finding_type",
                    "N/A",
                ),
                finding.get(
                    "soc_id",
                    "N/A",
                ),
                finding.get(
                    "alert_id",
                    "—",
                ),
                finding.get(
                    "case_id",
                    "—",
                ),
                finding.get(
                    "rule_id",
                    "N/A",
                ),
                finding.get(
                    "assigned_analyst_id",
                    "—",
                ),
            ]

            for column, value in enumerate(
                values
            ):
                self.finding_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        str(value)
                    ),
                )

            # Store complete finding
            self.finding_table.item(
                row,
                0,
            ).setData(
                Qt.UserRole,
                finding,
            )

        self.finding_detail_title.setText(
            f"{len(filtered)} finding(s)"
        )

        self.finding_detail.clear()

    def finding_selected(self):
        rows = self.finding_table.selectedItems()

        if not rows:
            return

        item = self.finding_table.item(
            rows[0].row(),
            0,
        )

        finding = item.data(
            Qt.UserRole
        )

        if not finding:
            return

        self.show_finding_detail(
            finding
        )

    def show_finding_detail(self, finding):
        finding_type = finding.get(
            "finding_type",
            "UNKNOWN",
        )

        severity = finding.get(
            "severity",
            "N/A",
        )

        category = finding.get(
            "_category",
            "Finding",
        )

        self.finding_detail_title.setText(
            f"{severity} — {finding_type}"
        )

        evidence = finding.get(
            "evidence",
            {},
        )

        lines = [
            f"Category: {category}",
            f"SOC: {finding.get('soc_id', 'N/A')}",
            f"Finding Type: {finding_type}",
            f"Severity: {severity}",
            f"Rule: {finding.get('rule_id', 'N/A')}",
            f"Alert ID: {finding.get('alert_id', 'N/A')}",
            f"Case ID: {finding.get('case_id', 'N/A')}",
            f"Analyst: {finding.get('assigned_analyst_id', 'N/A')}",
            "",
            "RATIONALE",
            "────────────",
            finding.get(
                "rationale",
                "No rationale available.",
            ),
            "",
            "EVIDENCE",
            "────────────",
            json.dumps(
                evidence,
                indent=2,
            ),
        ]

        self.finding_detail.setPlainText(
            "\n".join(lines)
        )

    # ============================================================
    # REVIEW QUEUE
    # ============================================================

    def build_review_page(self):
        page = QWidget()

        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            30,
            25,
            30,
            25,
        )

        title = QLabel(
            "Supervisory Review Queue"
        )
        title.setObjectName(
            "page_title"
        )

        layout.addWidget(title)

        description = QLabel(
            "Prioritized findings requiring human supervisory review."
        )
        description.setObjectName(
            "page_description"
        )

        layout.addWidget(description)

        self.review_table = QTableWidget()
        self.review_table.setColumnCount(
            9
        )

        self.review_table.setHorizontalHeaderLabels(
            [
                "Queue",
                "Severity",
                "SOC",
                "Finding",
                "Rule",
                "Alert",
                "Case",
                "Queue Priority",
                "Case Priority",
            ]
        )

        self.review_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )

        self.review_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )

        self.review_table.itemSelectionChanged.connect(
            self.review_selected
        )

        layout.addWidget(
            self.review_table
        )

        self.review_detail = QTextEdit()
        self.review_detail.setReadOnly(
            True
        )

        layout.addWidget(
            self.review_detail
        )

        return page

    def refresh_review_queue(self):
        if not hasattr(
            self,
            "review_table",
        ):
            return

        self.review_table.setRowCount(
            len(self.review_queue)
        )

        for row, item in enumerate(
            self.review_queue
        ):
            values = [
                item.get(
                    "queue_rank",
                    "N/A",
                ),
                item.get(
                    "severity",
                    "N/A",
                ),
                item.get(
                    "soc_id",
                    "N/A",
                ),
                item.get(
                    "finding_type",
                    "N/A",
                ),
                item.get(
                    "rule_id",
                    "N/A",
                ),
                item.get(
                    "alert_id",
                    "—",
                ),
                item.get(
                    "case_id",
                    "—",
                ),
                item.get(
                    "queue_priority",
                    "N/A",
                ),
                item.get(
                    "case_priority",
                    "N/A",
                ),
            ]

            for column, value in enumerate(
                values
            ):
                self.review_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        str(value)
                    ),
                )

            self.review_table.item(
                row,
                0,
            ).setData(
                Qt.UserRole,
                item,
            )

        self.review_detail.setPlainText(
            f"{len(self.review_queue)} item(s) "
            "in supervisory review queue."
        )

    def review_selected(self):
        rows = self.review_table.selectedItems()

        if not rows:
            return

        item = self.review_table.item(
            rows[0].row(),
            0,
        )

        review = item.data(
            Qt.UserRole
        )

        if not review:
            return

        evidence = review.get(
            "evidence",
            {},
        )

        text = [
            "SUPERVISORY REVIEW ITEM",
            "════════════════════════",
            "",
            f"Queue Rank: {review.get('queue_rank', 'N/A')}",
            f"Case Rank: {review.get('case_rank', 'N/A')}",
            f"SOC: {review.get('soc_id', 'N/A')}",
            f"Severity: {review.get('severity', 'N/A')}",
            f"Finding: {review.get('finding_type', 'N/A')}",
            f"Rule: {review.get('rule_id', 'N/A')}",
            f"Alert: {review.get('alert_id', 'N/A')}",
            f"Case: {review.get('case_id', 'N/A')}",
            f"Analyst: {review.get('assigned_analyst_id', 'N/A')}",
            "",
            f"Finding Weight: {review.get('finding_weight', 'N/A')}",
            f"Severity Boost: {review.get('severity_boost', 'N/A')}",
            f"Queue Priority: {review.get('queue_priority', 'N/A')}",
            f"Case Priority: {review.get('case_priority', 'N/A')}",
            f"Case Finding Count: {review.get('case_finding_count', 'N/A')}",
            "",
            "RATIONALE",
            "────────────",
            review.get(
                "rationale",
                "Unavailable",
            ),
            "",
            "EVIDENCE",
            "────────────",
            json.dumps(
                evidence,
                indent=2,
            ),
        ]

        self.review_detail.setPlainText(
            "\n".join(text)
        )

    # ============================================================
    # REPORTS
    # ============================================================

    def build_reports_page(self):
        page = QWidget()

        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            35,
            30,
            35,
            30,
        )

        title = QLabel("Reports")
        title.setObjectName(
            "page_title"
        )

        layout.addWidget(title)

        description = QLabel(
            "Assessment outputs generated locally by SAT-SA."
        )
        description.setObjectName(
            "page_description"
        )

        layout.addWidget(description)

        self.report_status = QLabel(
            "No assessment outputs loaded."
        )

        layout.addWidget(
            self.report_status
        )

        self.report_list = QVBoxLayout()
        layout.addLayout(
            self.report_list
        )

        layout.addStretch()

        return page

    def refresh_reports(self):
        if not hasattr(
            self,
            "report_list",
        ):
            return

        # Remove old widgets
        while self.report_list.count():
            item = self.report_list.takeAt(0)

            widget = item.widget()

            if widget:
                widget.deleteLater()

        if not self.output_dir.exists():
            self.report_status.setText(
                "No assessment output directory found."
            )
            return

        files = [
            (
                "Executive Summary",
                "executive_summary.pdf",
            ),
            (
                "Assessment Results",
                "assessment_results.json",
            ),
            (
                "Entity Risk Scores",
                "entity_risk_scores.csv",
            ),
            (
                "Execution Gap Findings",
                "execution_gap_findings.csv",
            ),
            (
                "Negative Space Findings",
                "negative_space_findings.csv",
            ),
            (
                "Supervisory Review Queue",
                "review_queue.csv",
            ),
        ]

        existing = 0

        for title, filename in files:
            path = self.output_dir / filename

            if not path.exists():
                continue

            existing += 1

            row = QFrame()
            row.setObjectName(
                "report_row"
            )

            row_layout = QHBoxLayout(row)

            label = QLabel(title)

            path_label = QLabel(
                filename
            )

            path_label.setObjectName(
                "report_filename"
            )

            button = QPushButton(
                "OPEN"
            )

            button.setCursor(
                Qt.PointingHandCursor
            )

            button.clicked.connect(
                lambda checked=False,
                p=path: self.open_file(p)
            )

            row_layout.addWidget(
                label
            )

            row_layout.addStretch()

            row_layout.addWidget(
                path_label
            )

            row_layout.addWidget(
                button
            )

            self.report_list.addWidget(
                row
            )

        self.report_status.setText(
            f"{existing} report/output file(s) available."
        )

    def open_file(self, path):
        if not path.exists():
            self.notify(
                "warning",
                "File Not Found",
                str(path),
            )
            return

        QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                str(path)
            )
        )

    # ============================================================
    # DASHBOARD REFRESH
    # ============================================================

    def refresh_dashboard(self):
        if not self.current_result:
            return

        result = self.current_result

        entities = result.get(
            "entities",
            [],
        )

        execution_count = sum(
            len(
                entity.get(
                    "execution_gap_findings",
                    [],
                )
            )
            for entity in entities
        )

        negative_count = sum(
            len(
                entity.get(
                    "negative_space_findings",
                    [],
                )
            )
            for entity in entities
        )

        total_findings = (
            execution_count
            + negative_count
        )

        review_count = len(
            result.get(
                "review_queue",
                [],
            )
        )

        self.kpi_socs.value_label.setText(
            str(len(entities))
        )

        self.kpi_findings.value_label.setText(
            str(total_findings)
        )

        self.kpi_execution.value_label.setText(
            str(execution_count)
        )

        self.kpi_negative.value_label.setText(
            str(negative_count)
        )

        self.kpi_review.value_label.setText(
            str(review_count)
        )

        self.dashboard_status.setText(
            "Assessment loaded successfully."
        )

        # Risk ranking
        ranked = sorted(
            entities,
            key=lambda x: x.get(
                "priority_rank",
                999999,
            ),
        )

        self.risk_table.setRowCount(
            len(ranked)
        )

        for row, entity in enumerate(
            ranked
        ):
            values = [
                entity.get(
                    "priority_rank",
                    "N/A",
                ),
                entity.get(
                    "soc_id",
                    "N/A",
                ),
                entity.get(
                    "organization_name",
                    "N/A",
                ),
                entity.get(
                    "peer_group",
                    "N/A",
                ),
                entity.get(
                    "supervisory_risk_score",
                    "N/A",
                ),
                entity.get(
                    "execution_gap_count",
                    0,
                ),
            ]

            for column, value in enumerate(
                values
            ):
                self.risk_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        str(value)
                    ),
                )

    # ============================================================
    # STYLES
    # ============================================================

    VALIDATION_STYLES = """
            QLabel#validation_status {
                padding: 9px 12px;
                border-radius: 4px;
                font-weight: 600;
                background-color: #1b2431;
                color: #93a4bd;
                border-left: 3px solid #3d4b5f;
            }
            QLabel#validation_status[state="running"] {
                color: #cfd8e6;
                border-left: 3px solid #4a6fa5;
            }
            QLabel#validation_status[state="ok"] {
                color: #7fd1a0;
                border-left: 3px solid #2e7d52;
            }
            QLabel#validation_status[state="warning"] {
                color: #e8c07d;
                border-left: 3px solid #a8792c;
            }
            QLabel#validation_status[state="error"] {
                color: #f08a8a;
                border-left: 3px solid #a63d3d;
            }
            QTextEdit#validation_detail {
                background-color: #141b26;
                border: 1px solid #2b3648;
                border-radius: 4px;
                font-family: monospace;
                font-size: 11px;
                padding: 6px;
            }
    """

    def apply_styles(self):
        self.setStyleSheet(
            self.VALIDATION_STYLES
            + """
            QMainWindow {
                background: #0b0f14;
                color: #e6edf3;
            }

            QWidget {
                font-family: "Inter", "Arial";
                font-size: 13px;
            }

            #sidebar {
                background: #0d1219;
                border-right: 1px solid #202938;
            }

            #sidebar_title {
                font-size: 27px;
                font-weight: 800;
                color: #f0f6fc;
            }

            #sidebar_subtitle {
                color: #7d8998;
                font-size: 11px;
            }

            #nav_button {
                background: transparent;
                border: none;
                border-radius: 7px;
                color: #9aa6b2;
                text-align: left;
                padding-left: 15px;
                font-weight: 600;
            }

            #nav_button:hover {
                background: #171e29;
                color: #ffffff;
            }

            #nav_button[active="true"] {
                background: #1c2633;
                color: #ffffff;
            }

            #offline_status {
                background: #101923;
                border: 1px solid #263344;
                border-radius: 6px;
                padding: 8px;
                color: #71d49b;
                font-size: 10px;
                font-weight: 700;
            }

            #page_title {
                font-size: 28px;
                font-weight: 800;
                color: #f0f6fc;
            }

            #page_description {
                color: #8b98a8;
                font-size: 13px;
            }

            #section_title {
                font-size: 17px;
                font-weight: 700;
                color: #dce5ef;
                margin-top: 8px;
            }

            #status_label {
                color: #8fa0b2;
                padding: 8px;
            }

            #kpi_card {
                background: #111821;
                border: 1px solid #222d3b;
                border-radius: 9px;
            }

            #kpi_title {
                color: #8492a3;
                font-size: 11px;
                font-weight: 600;
            }

            #kpi_value {
                color: #f0f6fc;
                font-size: 27px;
                font-weight: 800;
            }

            #drop_zone {
                background: #101720;
                border: 2px dashed #354354;
                border-radius: 12px;
                min-height: 190px;
            }

            #drop_zone:hover {
                border-color: #62748a;
            }

            #drop_title {
                color: #eaf1f8;
                font-size: 20px;
                font-weight: 800;
            }

            #drop_description {
                color: #7f8da0;
            }

            #drop_or {
                color: #667487;
            }

            #drop_status {
                color: #9aa8b8;
            }

            #browse_button {
                background: #182230;
                border: 1px solid #334154;
                border-radius: 6px;
                padding: 9px 18px;
                color: #dce5ee;
            }

            #browse_button:hover {
                background: #202d3d;
            }

            #primary_button {
                background: #e6edf3;
                color: #0b0f14;
                border: none;
                border-radius: 7px;
                font-weight: 800;
            }

            #primary_button:hover {
                background: #ffffff;
            }

            #primary_button:disabled {
                background: #56606d;
                color: #aab3bd;
            }

            QComboBox {
                background: #111821;
                border: 1px solid #293545;
                border-radius: 6px;
                padding: 9px;
                color: #dbe5ef;
            }

            QComboBox QAbstractItemView {
                background: #111821;
                color: #dbe5ef;
                selection-background-color: #263446;
            }

            QTableWidget {
                background: #0f151d;
                border: 1px solid #222d3b;
                gridline-color: #202936;
                color: #dce5ee;
                selection-background-color: #253447;
                selection-color: #ffffff;
            }

            QHeaderView::section {
                background: #151d27;
                color: #8fa0b2;
                border: none;
                border-bottom: 1px solid #283343;
                padding: 9px;
                font-weight: 700;
            }

            QTextEdit {
                background: #0f151d;
                border: 1px solid #252f3d;
                border-radius: 7px;
                color: #dbe5ef;
                padding: 10px;
            }

            #assessment_log {
                font-family: "JetBrains Mono", "DejaVu Sans Mono";
                color: #9eb0c1;
            }

            #detail_panel {
                background: #101720;
                border: 1px solid #222d3b;
                border-radius: 8px;
            }

            #detail_title {
                font-size: 18px;
                font-weight: 800;
                color: #edf4fa;
            }

            #report_row {
                background: #111821;
                border: 1px solid #222d3b;
                border-radius: 8px;
                padding: 5px;
            }

            #report_filename {
                color: #758497;
                font-size: 11px;
            }

            #report_row QPushButton {
                background: #1a2634;
                border: 1px solid #324255;
                border-radius: 5px;
                padding: 7px 15px;
                color: #dce6ef;
                font-weight: 700;
            }

            #report_row QPushButton:hover {
                background: #243448;
            }

            QScrollBar:vertical {
                background: #0d1219;
                width: 10px;
            }

            QScrollBar::handle:vertical {
                background: #354354;
                border-radius: 5px;
            }
            """
        )


def main():
    app = QApplication.instance()

    if app is None:
        app = QApplication([])

    app.setApplicationName("SAT-SA")
    app.setOrganizationName("SAT-SA")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    main()