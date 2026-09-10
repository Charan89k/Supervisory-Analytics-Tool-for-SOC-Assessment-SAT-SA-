from pathlib import Path
import json

from PySide6.QtCore import QByteArray, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
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

from analytics.narration.base import STATE_DISABLED, BackendStatus
from analytics.ingestion import describe_supported_inputs
from application.services import (
    dashboard_service,
    review_service,
    rule_reference,
)
from application.services.evidence_service import (
    EvidenceService,
    SourceUnavailable,
)
from application.session import Phase, SessionState
from application.version import (
    APP_FULL_NAME,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
)
from application.widgets.app_icon import application_icon
from application.narration_worker import NarrationWorker
from application.pages.settings_page import SettingsPage
from application.services.narration_service import NarrationService
from application.services.settings_service import SettingsService
from analytics import trends
from application.widgets.charts import (
    make_bar_chart,
    make_trend_chart,
    severity_color,
)
from application.widgets.finding_detail import FindingDetailPanel
from application.widgets.drop_zone import DropZone
from application.worker import AssessmentWorker, ValidationWorker
from application.services.ai_config import AIConfig, create_ai_config


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

        self.setWindowTitle(f"{APP_FULL_NAME}  —  v{APP_VERSION}")
        self.setWindowIcon(application_icon())
        self.resize(1450, 900)

        # One object answers "where is this session?". Every page gate
        # and status readout reads from it rather than inferring an
        # answer from whichever attributes happen to be set.
        self.session = SessionState()
        self.session.changed.connect(self.on_phase_changed)

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

        self.current_finding = None
        self.trend_report = None
        self._assessment_config = None
        # Re-reads the submission on demand for source-record drill-down.
        # Cached for the session; reset when a new assessment runs.
        self.evidence_service = EvidenceService()

        self.narration_thread = None
        self.narration_worker = None
        self.narration_busy = False
        self.narration_outcome = None

        self.settings_service = SettingsService()
        self.ai_config = self.settings_service.load_ai_config()

        self.findings = []
        self.review_queue = []

        self.project_root = Path(__file__).resolve().parents[1]
        self.output_dir = self.project_root / "outputs" / "desktop_assessment"

        self.build_ui()
        self.build_menu_bar()
        self.build_status_bar()
        self.apply_styles()
        self.restore_window_state()

        # Reflect the launch phase once everything exists.
        self.on_phase_changed(self.session.phase)

        # The first AI probe is deferred past the initial paint. On the
        # Ollama backend it is an HTTP call with a multi-second timeout,
        # and doing it inline meant the window did not appear until it
        # returned. Nothing depends on the result being ready sooner.
        QTimer.singleShot(0, self.refresh_ai_status)

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
        self.settings_button = self.make_nav_button("Settings")

        sidebar_layout.addWidget(self.dashboard_button)
        sidebar_layout.addWidget(self.new_assessment_button)
        sidebar_layout.addWidget(self.findings_button)
        sidebar_layout.addWidget(self.review_button)
        sidebar_layout.addWidget(self.reports_button)
        sidebar_layout.addWidget(self.settings_button)

        sidebar_layout.addStretch()

        self.sidebar_ai_status = QLabel("AI: checking…")
        self.sidebar_ai_status.setObjectName("ai_status")
        self.sidebar_ai_status.setWordWrap(True)
        self.sidebar_ai_status.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(self.sidebar_ai_status)

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
        self.settings_page = SettingsPage(self.ai_config)
        self.settings_page.settings_saved.connect(self.ai_settings_saved)

        self.pages.addWidget(self.dashboard_page)
        self.pages.addWidget(self.assessment_page)
        self.pages.addWidget(self.findings_page)
        self.pages.addWidget(self.review_page)
        self.pages.addWidget(self.reports_page)
        self.pages.addWidget(self.settings_page)

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
        self.settings_button.clicked.connect(
            lambda: self.show_page(5)
        )

        self.show_page(0)
        self.refresh_ai_status()

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

    # ============================================================
    # MENU BAR / STATUS BAR
    # ============================================================

    def build_menu_bar(self):
        menus = self.menuBar()

        assessment_menu = menus.addMenu("&Assessment")

        self.action_new = QAction("&New Assessment", self)
        self.action_new.setShortcut(QKeySequence.New)
        self.action_new.triggered.connect(lambda: self.show_page(1))
        assessment_menu.addAction(self.action_new)

        self.action_run = QAction("&Run Assessment", self)
        self.action_run.setShortcut("Ctrl+R")
        self.action_run.triggered.connect(self.start_assessment)
        assessment_menu.addAction(self.action_run)

        assessment_menu.addSeparator()

        self.action_open_outputs = QAction("Open &Output Folder", self)
        self.action_open_outputs.triggered.connect(self.open_output_folder)
        assessment_menu.addAction(self.action_open_outputs)

        assessment_menu.addSeparator()

        action_quit = QAction("E&xit", self)
        action_quit.setShortcut(QKeySequence.Quit)
        action_quit.triggered.connect(self.close)
        assessment_menu.addAction(action_quit)

        view_menu = menus.addMenu("&View")
        self.view_actions = []
        for index, (label, shortcut) in enumerate([
            ("&Dashboard", "Ctrl+1"),
            ("New &Assessment", "Ctrl+2"),
            ("&Findings", "Ctrl+3"),
            ("Review &Queue", "Ctrl+4"),
            ("&Reports", "Ctrl+5"),
            ("&Settings", "Ctrl+6"),
        ]):
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(
                lambda checked=False, i=index: self.show_page(i))
            view_menu.addAction(action)
            self.view_actions.append(action)

        help_menu = menus.addMenu("&Help")

        action_about = QAction(f"&About {APP_NAME}", self)
        action_about.triggered.connect(self.show_about)
        help_menu.addAction(action_about)

        action_system = QAction("&System Information", self)
        action_system.triggered.connect(self.show_system_information)
        help_menu.addAction(action_system)

    def build_status_bar(self):
        """
        Persistent context that no single page owns: which dataset is
        loaded, where the session is, and whether AI is usable. Before
        this it was only visible on whichever page happened to show it.
        """
        bar = self.statusBar()

        self.status_phase = QLabel("")
        self.status_dataset = QLabel("")
        self.status_ai = QLabel("")
        self.status_offline = QLabel("OFFLINE / AIR-GAPPED")
        self.status_offline.setObjectName("status_offline")

        for widget in (self.status_phase, self.status_dataset):
            widget.setObjectName("status_item")
            bar.addWidget(widget)

        bar.addPermanentWidget(self.status_ai)
        bar.addPermanentWidget(self.status_offline)
        self.status_ai.setObjectName("status_item")

    def refresh_status_bar(self):
        self.status_phase.setText(f"  {self.session.status_label}")

        if self.session.dataset_label:
            self.status_dataset.setText(f"|  {self.session.dataset_label}")
        else:
            self.status_dataset.setText("|  No dataset selected")

        if self.session.last_assessment_at:
            self.status_dataset.setText(
                self.status_dataset.text()
                + f"  |  Last assessment {self.session.last_assessment_at}")

    def open_output_folder(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def show_about(self):
        self.notify(
            "info",
            f"About {APP_NAME}",
            f"{APP_FULL_NAME}\n"
            f"Version {APP_VERSION}\n\n"
            f"{APP_TAGLINE}\n\n"
            "Findings are produced by deterministic, auditable rules. "
            "Every threshold is published in the assessment configuration "
            "and every finding traces to the records behind it.\n\n"
            "The optional AI layer only restates findings the rule engine "
            "has already decided. It cannot create, remove, or re-score "
            "one.",
        )

    def show_system_information(self):
        import platform
        import sys

        status = self.current_ai_status()

        self.notify(
            "info",
            "System Information",
            f"{APP_NAME} {APP_VERSION}\n\n"
            f"Python           {sys.version.split()[0]}\n"
            f"Platform         {platform.system()} {platform.release()}\n"
            f"Machine          {platform.machine()}\n\n"
            f"Settings file    {self.settings_service.path}\n"
            f"Output folder    {self.output_dir}\n\n"
            f"Dataset formats  {describe_supported_inputs()}\n\n"
            f"AI backend       {status.backend}\n"
            f"AI status        {status.label()}\n\n"
            "Network          not used. SAT-SA makes no external "
            "connections; the optional AI layer runs on this machine only.",
        )

    # ============================================================
    # LIFECYCLE
    # ============================================================

    def on_phase_changed(self, phase):
        """
        Single place that reacts to a lifecycle transition. Page gating,
        the status bar, and the run action all follow from the phase
        rather than each testing attributes for themselves.
        """
        has_results = self.session.has_results

        for button in (self.findings_button, self.review_button,
                        self.reports_button):
            button.setEnabled(has_results)

        for index in (2, 3, 4):
            self.view_actions[index].setEnabled(has_results)

        self.run_button.setEnabled(
            self.session.can_run_assessment and not self.session.is_busy)
        self.action_run.setEnabled(self.run_button.isEnabled())

        for placeholder in (self.findings_placeholder,
                             self.review_placeholder,
                             self.reports_placeholder):
            placeholder.setText(self.empty_state_text())

        self.update_page_visibility()
        self.refresh_status_bar()

        # A results page the supervisor is standing on when results go
        # away would otherwise show a stale table.
        if not has_results and self.pages.currentIndex() in (2, 3, 4):
            self.show_page(0)

    def empty_state_text(self) -> str:
        return (
            "No assessment loaded.\n\n"
            f"{self.session.guidance}\n\n"
            "Go to New Assessment, choose a submission, and run it. "
            "Findings, the review queue and reports appear here once an "
            "assessment completes."
        )

    def update_page_visibility(self):
        """Swap each results page between its placeholder and its content."""
        has_results = self.session.has_results
        for placeholder, content in (
            (self.findings_placeholder, self.findings_content),
            (self.review_placeholder, self.review_content),
            (self.reports_placeholder, self.reports_content),
        ):
            placeholder.setVisible(not has_results)
            content.setVisible(has_results)

    # ============================================================
    # WINDOW STATE
    # ============================================================

    def restore_window_state(self):
        """
        Reopen where the supervisor left off. Any failure here is
        ignored: a remembered window position is a convenience and must
        never be a reason the application will not start.
        """
        stored = self.settings_service.load_window_state()

        geometry = stored.get("geometry")
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(
                    geometry.encode("ascii")))
            except Exception:
                pass

        index = stored.get("page_index", 0)
        # Never restore onto a results page: at launch there are no
        # results, and the page would open on its empty state.
        if isinstance(index, int) and index in (0, 1, 5):
            self.show_page(index)

    def save_window_state(self):
        try:
            self.settings_service.save_window_state(
                geometry_b64=bytes(
                    self.saveGeometry().toBase64()).decode("ascii"),
                page_index=self.pages.currentIndex(),
            )
        except Exception:
            pass

    def closeEvent(self, event):
        self.save_window_state()
        super().closeEvent(event)

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
            self.settings_button,
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
        """
        The supervisory dashboard, organised around the four questions a
        supervisor actually arrives with:

            WHO needs attention  -> entity risk ranking
            WHY                  -> risk drivers for the selected entity
            WHAT EVIDENCE        -> severity and category distribution
            WHAT TO REVIEW       -> top of the review queue

        Selecting a row in the ranking updates the WHY and WHAT EVIDENCE
        panels, so the page reads as one movement from "which entity" to
        "on what basis" rather than as unrelated tiles.
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(30, 22, 30, 22)
        outer.setSpacing(14)

        header = QLabel("Supervisory Dashboard")
        header.setObjectName("page_title")
        outer.addWidget(header)

        description = QLabel(
            "Supervisory analytics over submitted SOC records. This tool "
            "assesses how an entity ran its own security operations; it "
            "is not a SOC, a SIEM, or a monitoring system, and nothing "
            "here is live."
        )
        description.setObjectName("page_description")
        description.setWordWrap(True)
        outer.addWidget(description)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(16)

        self.dashboard_status = QLabel("No assessment loaded.")
        self.dashboard_status.setObjectName("status_label")
        layout.addWidget(self.dashboard_status)

        # ---- Executive overview -----------------------------------
        layout.addWidget(self.section_title("Executive Overview"))

        kpi_row_one = QHBoxLayout()
        kpi_row_one.setSpacing(12)
        self.kpi_socs = self.make_kpi_card("SOC Entities", "0")
        self.kpi_findings = self.make_kpi_card("Total Findings", "0")
        self.kpi_execution = self.make_kpi_card("Execution Gaps", "0")
        self.kpi_negative = self.make_kpi_card("Negative Space", "0")
        for card in (self.kpi_socs, self.kpi_findings,
                      self.kpi_execution, self.kpi_negative):
            kpi_row_one.addWidget(card)
        layout.addLayout(kpi_row_one)

        kpi_row_two = QHBoxLayout()
        kpi_row_two.setSpacing(12)
        self.kpi_critical = self.make_kpi_card("Critical Findings", "0")
        self.kpi_high = self.make_kpi_card("High Findings", "0")
        self.kpi_anomalies = self.make_kpi_card("Anomalies", "0")
        self.kpi_review = self.make_kpi_card("Priority Reviews", "0")
        self.kpi_explained = self.make_kpi_card("AI Explained", "0")
        for card in (self.kpi_critical, self.kpi_high, self.kpi_anomalies,
                      self.kpi_review, self.kpi_explained):
            kpi_row_two.addWidget(card)
        layout.addLayout(kpi_row_two)

        # ---- WHO ---------------------------------------------------
        layout.addWidget(self.section_title(
            "Entity Risk Ranking", "Which entities need supervisory attention"))

        self.risk_table = QTableWidget()
        self.risk_table.setColumnCount(9)
        self.risk_table.setHorizontalHeaderLabels([
            "Rank", "Entity", "Organization", "Peer Group", "Risk Score",
            "Percentile", "vs Peers", "Critical", "High",
        ])
        self.risk_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.risk_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.risk_table.setSelectionMode(QTableWidget.SingleSelection)
        self.risk_table.verticalHeader().setVisible(False)
        self.risk_table.horizontalHeader().setStretchLastSection(True)
        self.risk_table.setMinimumHeight(190)
        self.risk_table.itemSelectionChanged.connect(self.dashboard_entity_changed)
        layout.addWidget(self.risk_table)

        self.peer_note = QLabel("")
        self.peer_note.setObjectName("caveat")
        self.peer_note.setWordWrap(True)
        layout.addWidget(self.peer_note)

        # ---- WHY + WHAT EVIDENCE -----------------------------------
        self.driver_title = self.section_title(
            "Risk Drivers & Evidence",
            "Why the selected entity ranks where it does — both panels "
            "follow the row selected above")
        layout.addWidget(self.driver_title)

        panels = QHBoxLayout()
        panels.setSpacing(14)

        driver_panel = QFrame()
        driver_panel.setObjectName("panel")
        driver_layout = QVBoxLayout(driver_panel)
        self.driver_chart = make_bar_chart(
            "Score contribution by finding type", height=210)
        driver_layout.addWidget(self.driver_chart)
        self.driver_note = QLabel("")
        self.driver_note.setObjectName("caveat")
        self.driver_note.setWordWrap(True)
        driver_layout.addWidget(self.driver_note)
        panels.addWidget(driver_panel, 5)

        distribution_panel = QFrame()
        distribution_panel.setObjectName("panel")
        distribution_layout = QVBoxLayout(distribution_panel)
        self.severity_chart = make_bar_chart(
            "Findings by severity", height=170, left_margin=58)
        self.category_chart = make_bar_chart(
            "Findings by category", height=110, left_margin=58)
        distribution_layout.addWidget(self.severity_chart)
        distribution_layout.addWidget(self.category_chart)
        panels.addWidget(distribution_panel, 4)

        layout.addLayout(panels)

        # ---- WHAT TO REVIEW ----------------------------------------
        layout.addWidget(self.section_title(
            "Priority Review Queue",
            "Where a supervisor should spend manual review effort first"))

        self.review_preview_table = QTableWidget()
        self.review_preview_table.setColumnCount(6)
        self.review_preview_table.setHorizontalHeaderLabels([
            "Rank", "Entity", "Severity", "Finding", "Rule",
            "Why prioritised",
        ])
        self.review_preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.review_preview_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.review_preview_table.verticalHeader().setVisible(False)
        self.review_preview_table.horizontalHeader().setStretchLastSection(True)
        self.review_preview_table.setMinimumHeight(230)
        layout.addWidget(self.review_preview_table)

        open_queue = QPushButton("Open full review queue")
        open_queue.setCursor(Qt.PointingHandCursor)
        open_queue.clicked.connect(lambda: self.show_page(3))
        layout.addWidget(open_queue, 0, Qt.AlignLeft)

        # ---- Trends -------------------------------------------------
        layout.addWidget(self.section_title(
            "Trends", "How entities changed across submission periods"))

        self.trend_note = QLabel("")
        self.trend_note.setObjectName("caveat")
        self.trend_note.setWordWrap(True)
        layout.addWidget(self.trend_note)

        self.trend_panel = QFrame()
        self.trend_panel.setObjectName("panel")
        trend_layout = QVBoxLayout(self.trend_panel)

        self.trend_chart = make_trend_chart("Supervisory risk score")
        trend_layout.addWidget(self.trend_chart)

        self.trend_table = QTableWidget()
        self.trend_table.setColumnCount(7)
        self.trend_table.setHorizontalHeaderLabels([
            "Entity", "First", "Latest", "Change", "Direction",
            "Ranking", "Findings"])
        self.trend_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.trend_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.trend_table.verticalHeader().setVisible(False)
        self.trend_table.horizontalHeader().setStretchLastSection(True)
        self.trend_table.setMinimumHeight(180)
        trend_layout.addWidget(self.trend_table)

        self.persistent_note = QLabel("")
        self.persistent_note.setObjectName("caveat")
        self.persistent_note.setWordWrap(True)
        trend_layout.addWidget(self.persistent_note)

        self.trend_panel.hide()
        layout.addWidget(self.trend_panel)

        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        return page

    def section_title(self, text, subtitle=""):
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 6, 0, 0)
        box.setSpacing(1)

        title = QLabel(text)
        title.setObjectName("section_title")
        box.addWidget(title)

        if subtitle:
            hint = QLabel(subtitle)
            hint.setObjectName("section_subtitle")
            box.addWidget(hint)

        return holder

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

        # ---- AI explanations --------------------------------------
        # This used to be a model-picker combo that was wired to nothing
        # at all: it built a config, handed it to the worker, and the
        # worker handed it straight back unused. Model choice now lives
        # in Settings; what belongs on this page is the one decision
        # relevant to the run about to start.
        ai_title = QLabel("AI Explanations")
        ai_title.setObjectName("section_title")
        layout.addWidget(ai_title)

        ai_row = QHBoxLayout()

        self.ai_enabled_box = QCheckBox(
            "Explain top review-queue findings after the assessment"
        )
        self.ai_enabled_box.setChecked(self.ai_config.enabled)
        self.ai_enabled_box.toggled.connect(self.ai_toggle_changed)
        ai_row.addWidget(self.ai_enabled_box)

        ai_row.addStretch()

        self.ai_settings_button = QPushButton("AI Settings…")
        self.ai_settings_button.setCursor(Qt.PointingHandCursor)
        self.ai_settings_button.clicked.connect(lambda: self.show_page(5))
        ai_row.addWidget(self.ai_settings_button)

        layout.addLayout(ai_row)

        self.ai_status_label = QLabel("")
        self.ai_status_label.setObjectName("ai_status")
        self.ai_status_label.setWordWrap(True)
        layout.addWidget(self.ai_status_label)

        self.narration_progress = QProgressBar()
        self.narration_progress.setObjectName("narration_progress")
        self.narration_progress.setTextVisible(True)
        self.narration_progress.hide()
        layout.addWidget(self.narration_progress)

        self.cancel_narration_button = QPushButton("Cancel Explanations")
        self.cancel_narration_button.setCursor(Qt.PointingHandCursor)
        self.cancel_narration_button.clicked.connect(self.cancel_narration)
        self.cancel_narration_button.hide()
        layout.addWidget(self.cancel_narration_button)

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

        self.session.set_dataset(path, Path(path).name)
        self.refresh_status_bar()

        self.progress_label.setText(
            f"Dataset selected: {path}"
        )

        self.start_validation(path)

    def dataset_rejected(self, reason):
        """The drop zone was given something the engine cannot read."""
        self.dataset_path = None
        self.session.set_dataset(None)
        self.session.transition(Phase.NO_DATASET)

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
        self.session.transition(Phase.VALIDATING)
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
            self.session.transition(Phase.INVALID)
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

        self.session.transition(Phase.READY)

    def validation_error(self, message):
        self.validation_busy = False
        self.session.transition(Phase.NO_DATASET)
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

        # The deterministic assessment never consults this; it is carried
        # so the narration step that follows knows what the supervisor
        # asked for.
        ai_config = self.ai_config
        self.current_ai_config = ai_config

        self.assessment_busy = True
        self.session.transition(Phase.ASSESSING)
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
        import datetime

        self.assessment_busy = False
        self.current_result, self.current_ai_config, self.trend_report = result
        self.session.last_assessment_at = (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

        self.progress_label.setText(
            "Assessment completed."
        )

        self.assessment_log.append(
            ""
        )
        self.assessment_log.append(
            "✓ Assessment completed successfully."
        )

        # A new assessment may come from a different submission, so the
        # cached source data must not be reused across runs.
        self.evidence_service.reset()
        self.current_finding = None

        # Process all generated data
        self.prepare_result_data()

        # Results now exist: the results pages become reachable.
        self.session.transition(Phase.LOADED)

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

        # Narration runs only after the assessment is complete and its
        # outputs are on disk. Nothing it does can change them.
        self.maybe_start_narration()

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
        self.session.transition(Phase.INVALID)

        self.progress_label.setText("Assessment stopped — dataset validation failed.")
        self.assessment_log.append("")
        self.assessment_log.append("\u2717 Dataset validation failed. Assessment not run.")
        for issue in report.issues:
            rows = f" ({issue.row_count} rows)" if issue.row_count else ""
            self.assessment_log.append(
                f"    [{issue.severity}] {issue.table}: {issue.message}{rows}"
            )

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

    # ============================================================
    # AI EXPLANATIONS
    # ============================================================

    def ai_settings_saved(self, config):
        """Settings page committed a new AI configuration."""
        self.ai_config = config
        self.settings_service.save_ai_config(config)

        self.ai_enabled_box.blockSignals(True)
        self.ai_enabled_box.setChecked(config.enabled)
        self.ai_enabled_box.blockSignals(False)

        self.refresh_ai_status()
        self.notify(
            "info",
            "Settings Saved",
            f"AI settings saved to:\n{self.settings_service.path}",
        )

    def ai_toggle_changed(self, enabled):
        """The checkbox on the assessment page mirrors the stored config."""
        self.ai_config.enabled = enabled
        self.settings_service.save_ai_config(self.ai_config)
        self.settings_page.load_from(self.ai_config)
        self.refresh_ai_status()

    def current_ai_status(self) -> BackendStatus:
        return NarrationService(config=self.ai_config).status()

    def refresh_ai_status(self):
        """
        Probe the configured backend and show the result.

        Cheap by construction: probe() checks for a file or pings a
        loopback port against a short timeout of its own. It never loads
        a model, so this is safe to call whenever settings change.
        """
        status = self.current_ai_status()

        for label in (self.ai_status_label, self.sidebar_ai_status):
            label.setProperty("state", status.resolved_state())
            label.style().unpolish(label)
            label.style().polish(label)

        self.ai_status_label.setText(status.label())
        self.sidebar_ai_status.setText(status.headline())

        return status

    def maybe_start_narration(self):
        """
        Run the explanation pass, if the supervisor asked for one and a
        backend is actually usable.

        Called only after the assessment has completed and its outputs
        are already written to disk, so nothing that happens here can
        affect the assessment's result.
        """
        if not self.ai_config.enabled:
            return

        status = self.refresh_ai_status()
        if not status.available:
            self.assessment_log.append(
                f"\u26a0 AI explanations skipped — {status.detail}. "
                "Every finding keeps its rule-generated rationale."
            )
            return

        if not self.review_queue:
            return

        self.narration_busy = True
        self.narration_progress.setValue(0)
        self.narration_progress.setMaximum(
            min(self.ai_config.max_explanations, len(self.review_queue)))
        self.narration_progress.setFormat("Explaining %v of %m findings…")
        self.narration_progress.show()
        self.cancel_narration_button.setEnabled(True)
        self.cancel_narration_button.show()

        self.narration_thread = QThread()
        self.narration_worker = NarrationWorker(
            self.review_queue, self.ai_config)
        self.narration_worker.moveToThread(self.narration_thread)

        self.narration_thread.started.connect(self.narration_worker.run)
        self.narration_worker.progress.connect(self.narration_progress_update)
        self.narration_worker.finished.connect(self.narration_finished)
        self.narration_worker.failed.connect(self.narration_failed)

        self.narration_worker.finished.connect(self.narration_thread.quit)
        self.narration_worker.failed.connect(self.narration_thread.quit)
        self.narration_thread.finished.connect(
            self.narration_worker.deleteLater)
        self.narration_thread.finished.connect(
            self.narration_thread.deleteLater)

        self.narration_thread.start()

    def narration_progress_update(self, done, total):
        self.narration_progress.setMaximum(total)
        self.narration_progress.setValue(done)

    def cancel_narration(self):
        """
        Ask the worker to stop after the explanation in flight.

        The worker is not killed: a backend call is a blocking network
        or inference call, and interrupting it mid-flight would leave a
        half-written record. It checks the flag between findings, so a
        cancel lands within one explanation.
        """
        if self.narration_worker is not None:
            self.narration_worker.cancel()
            self.cancel_narration_button.setEnabled(False)
            self.cancel_narration_button.setText("Cancelling…")
            self.assessment_log.append(
                "\u2026 Cancelling explanations after the current finding."
            )

    def narration_finished(self, outcome):
        self.narration_busy = False
        self.narration_outcome = outcome

        self.narration_progress.hide()
        self.cancel_narration_button.hide()
        self.cancel_narration_button.setText("Cancel Explanations")
        self.cancel_narration_button.setEnabled(True)

        self.assessment_log.append(f"\u2713 {outcome.summary()}")

        # The explanations were written onto the review-queue records in
        # place; re-render whatever is showing them.
        self.refresh_review_queue()
        self.refresh_findings()
        self.refresh_dashboard()

    def narration_failed(self, message):
        self.narration_busy = False

        self.narration_progress.hide()
        self.cancel_narration_button.hide()

        # The assessment is already complete and saved. A narration
        # failure is reported, never escalated into an assessment
        # failure.
        self.assessment_log.append(
            f"\u26a0 AI explanations failed: {message}. "
            "The assessment itself is unaffected."
        )
        self.notify(
            "warning",
            "AI Explanations Failed",
            f"{message}\n\nThe deterministic assessment completed "
            "normally and all findings keep their rule-generated "
            "rationale.",
        )

    def assessment_failed(self, message):
        self.assessment_busy = False
        # Back to READY: the dataset validated, the run did not. The
        # supervisor can correct the cause and run again without
        # re-selecting the submission.
        self.session.transition(Phase.READY)

        self.progress_label.setText(
            "Assessment failed."
        )

        self.assessment_log.append(
            f"✗ ERROR: {message}"
        )

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
        """
        The Findings explorer.

        Laid out so the audit chain is readable top to bottom in one
        panel — Finding, Rule, Rationale, Evidence, Source records — with
        no step hidden behind a tab.

        The AI explanation lives in its OWN widget below the
        deterministic detail, never inside it. That is structural, not
        cosmetic: with separate widgets there is no code path by which
        narration text can be written into the authoritative panel.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 25, 30, 25)

        title = QLabel("Findings")
        title.setObjectName("page_title")
        layout.addWidget(title)

        description = QLabel(
            "Evidence-backed execution gaps and negative-space findings. "
            "Every finding traces to the rule that produced it, the "
            "evidence that rule read, and the submitted records behind "
            "that evidence."
        )
        description.setObjectName("page_description")
        description.setWordWrap(True)
        layout.addWidget(description)

        page_layout = layout

        self.findings_placeholder = QLabel("")
        self.findings_placeholder.setObjectName("empty_state")
        self.findings_placeholder.setAlignment(Qt.AlignCenter)
        self.findings_placeholder.setWordWrap(True)
        page_layout.addWidget(self.findings_placeholder, 1)

        self.findings_content = QWidget()
        layout = QVBoxLayout(self.findings_content)
        layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(self.findings_content, 1)

        # ---- Search + filters --------------------------------------
        search_row = QHBoxLayout()
        self.finding_search = QLineEdit()
        self.finding_search.setPlaceholderText(
            "Search rule, finding type, alert, case, analyst, entity, "
            "or rationale text…")
        self.finding_search.setClearButtonEnabled(True)
        self.finding_search.textChanged.connect(self.refresh_findings)
        search_row.addWidget(self.finding_search, 1)

        self.finding_count_label = QLabel("")
        self.finding_count_label.setObjectName("caveat")
        # Fixed vertically: a Preferred-height QLabel leaves the row's
        # maximum height unbounded, and QVBoxLayout then hands the row
        # every spare pixel — the search box ended up centred in a
        # 400px band.
        self.finding_count_label.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed)
        search_row.addWidget(self.finding_count_label)
        layout.addLayout(search_row)

        filters = QHBoxLayout()
        filters.setSpacing(8)

        self.finding_category_filter = QComboBox()
        self.finding_category_filter.addItems(
            ["All Categories", "Execution Gap", "Negative Space"])

        self.finding_soc_filter = QComboBox()
        self.finding_soc_filter.addItem("All Entities")

        self.finding_severity_filter = QComboBox()
        self.finding_severity_filter.addItems(
            ["All Severities", "CRITICAL", "HIGH", "MEDIUM", "LOW", "UNRATED"])

        self.finding_type_filter = QComboBox()
        self.finding_type_filter.addItem("All Finding Types")

        self.finding_rule_filter = QComboBox()
        self.finding_rule_filter.addItem("All Rules")

        self.finding_sort = QComboBox()
        self.finding_sort.addItems([
            "Sort: Severity (worst first)",
            "Sort: Entity",
            "Sort: Finding type",
            "Sort: Rule ID",
        ])

        for widget, label in (
            (self.finding_category_filter, "Category"),
            (self.finding_soc_filter, "Entity"),
            (self.finding_severity_filter, "Severity"),
            (self.finding_type_filter, "Type"),
            (self.finding_rule_filter, "Rule"),
            (self.finding_sort, "Sort"),
        ):
            widget.setMinimumWidth(150)
            widget.currentIndexChanged.connect(self.refresh_findings)
            filters.addWidget(widget)

        self.clear_filters_button = QPushButton("Clear")
        self.clear_filters_button.setCursor(Qt.PointingHandCursor)
        self.clear_filters_button.clicked.connect(self.clear_finding_filters)
        filters.addWidget(self.clear_filters_button)
        filters.addStretch()
        layout.addLayout(filters)

        # ---- Table + detail ----------------------------------------
        splitter = QSplitter(Qt.Horizontal)

        self.finding_table = QTableWidget()
        self.finding_table.setColumnCount(7)
        self.finding_table.setHorizontalHeaderLabels(
            ["Severity", "Category", "Entity", "Finding Type", "Rule",
             "Alert", "Analyst"])
        self.finding_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.finding_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.finding_table.setSelectionMode(QTableWidget.SingleSelection)
        self.finding_table.verticalHeader().setVisible(False)
        self.finding_table.itemSelectionChanged.connect(self.finding_selected)
        splitter.addWidget(self.finding_table)

        # ---- Detail panel -------------------------------------------
        # Shared with the Review Queue so the AI-separation rule has one
        # implementation rather than two that could drift apart.
        self.finding_detail_panel = FindingDetailPanel()
        self.finding_detail_panel.source_requested.connect(
            self.load_finding_source_records)
        splitter.addWidget(self.finding_detail_panel)
        splitter.setSizes([700, 640])
        layout.addWidget(splitter)

        return page

    def clear_finding_filters(self):
        for widget in (self.finding_category_filter, self.finding_soc_filter,
                        self.finding_severity_filter, self.finding_type_filter,
                        self.finding_rule_filter):
            widget.blockSignals(True)
            widget.setCurrentIndex(0)
            widget.blockSignals(False)
        self.finding_search.blockSignals(True)
        self.finding_search.clear()
        self.finding_search.blockSignals(False)
        self.refresh_findings()

    #: Direction is not a verdict — whether a rise is bad depends on the
    #: measure — so these read as movement, not as good and bad.
    TREND_COLOURS = {
        "up": "#ec835a",
        "down": "#0ca30c",
        "mixed": "#8c9ab0",
        "stable": "#8c9ab0",
    }

    SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3,
                      "UNRATED": 4}

    @staticmethod
    def finding_severity(finding) -> str:
        value = finding.get("severity")
        if value is None:
            return "UNRATED"
        text = str(value).strip().upper()
        return text if text in MainWindow.SEVERITY_RANK else "UNRATED"

    def _sync_filter_options(self):
        """Rebuild entity/type/rule choices from the loaded findings."""
        for widget, key, all_label in (
            (self.finding_soc_filter, "soc_id", "All Entities"),
            (self.finding_type_filter, "finding_type", "All Finding Types"),
            (self.finding_rule_filter, "rule_id", "All Rules"),
        ):
            current = widget.currentText()
            values = sorted({str(f.get(key)) for f in self.findings
                             if f.get(key)})
            widget.blockSignals(True)
            widget.clear()
            widget.addItem(all_label)
            widget.addItems(values)
            index = widget.findText(current)
            widget.setCurrentIndex(index if index >= 0 else 0)
            widget.blockSignals(False)

    def filtered_findings(self):
        """
        Apply search and filters. Pure selection — nothing here alters a
        finding, only decides whether it is shown.
        """
        category = self.finding_category_filter.currentText()
        soc = self.finding_soc_filter.currentText()
        severity = self.finding_severity_filter.currentText()
        finding_type = self.finding_type_filter.currentText()
        rule = self.finding_rule_filter.currentText()
        query = self.finding_search.text().strip().lower()

        results = []
        for finding in self.findings:
            if category != "All Categories" and finding.get("_category") != category:
                continue
            if soc != "All Entities" and str(finding.get("soc_id")) != soc:
                continue
            if severity != "All Severities" and self.finding_severity(finding) != severity:
                continue
            if finding_type != "All Finding Types" and str(finding.get("finding_type")) != finding_type:
                continue
            if rule != "All Rules" and str(finding.get("rule_id")) != rule:
                continue

            if query:
                haystack = " ".join(str(finding.get(key, "")) for key in (
                    "soc_id", "finding_type", "rule_id", "alert_id",
                    "case_id", "assigned_analyst_id", "severity",
                    "rationale")).lower()
                if query not in haystack:
                    continue

            results.append(finding)

        sort_mode = self.finding_sort.currentIndex()
        if sort_mode == 0:
            results.sort(key=lambda f: (
                self.SEVERITY_RANK[self.finding_severity(f)],
                str(f.get("soc_id")), str(f.get("finding_type"))))
        elif sort_mode == 1:
            results.sort(key=lambda f: (
                str(f.get("soc_id")),
                self.SEVERITY_RANK[self.finding_severity(f)]))
        elif sort_mode == 2:
            results.sort(key=lambda f: (
                str(f.get("finding_type")),
                self.SEVERITY_RANK[self.finding_severity(f)]))
        else:
            results.sort(key=lambda f: (
                str(f.get("rule_id")),
                self.SEVERITY_RANK[self.finding_severity(f)]))

        return results

    def refresh_findings(self):
        if not hasattr(self, "finding_table"):
            return

        self._sync_filter_options()
        filtered = self.filtered_findings()

        self.finding_count_label.setText(
            f"{len(filtered):,} of {len(self.findings):,} findings")

        self.finding_table.setSortingEnabled(False)
        self.finding_table.setRowCount(len(filtered))

        for row, finding in enumerate(filtered):
            severity = self.finding_severity(finding)
            cells = [
                severity,
                str(finding.get("_category", "")),
                str(finding.get("soc_id", "")),
                str(finding.get("finding_type", "")),
                str(finding.get("rule_id", "")),
                str(finding.get("alert_id") or "—"),
                str(finding.get("assigned_analyst_id") or "—"),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setForeground(QColor(severity_color(severity)))
                self.finding_table.setItem(row, column, item)

            self.finding_table.item(row, 0).setData(Qt.UserRole, finding)

        self.finding_table.resizeColumnsToContents()

        if not filtered:
            self.finding_detail_panel.clear("No findings match the filters")

    def finding_selected(self):
        rows = self.finding_table.selectedItems()
        if not rows:
            return
        item = self.finding_table.item(rows[0].row(), 0)
        finding = item.data(Qt.UserRole) if item else None
        if finding:
            self.show_finding_detail(finding)

    # ------------------------------------------------------------------
    # Finding detail — the audit chain
    # ------------------------------------------------------------------

    def show_finding_detail(self, finding):
        """Render the finding through the shared, read-only panel."""
        self.current_finding = finding
        self.finding_detail_panel.show_finding(
            finding, self.assessment_config(),
            narration=self.narration_for(finding))

    def assessment_config(self) -> dict:
        """The configuration the loaded assessment ran under."""
        if getattr(self, "_assessment_config", None) is None:
            try:
                import yaml
                with open(self.project_root / "config"
                           / "assessment_rules.yaml") as handle:
                    self._assessment_config = yaml.safe_load(handle)
            except Exception:
                self._assessment_config = {}
        return self._assessment_config

    # ------------------------------------------------------------------
    # Source records — one path, used by both explorer and queue
    # ------------------------------------------------------------------

    def load_finding_source_records(self):
        self.load_source_into(self.finding_detail_panel)

    def load_source_into(self, panel):
        """
        Re-read the submitted records behind the panel's finding.

        Read back from the dataset rather than from anything the
        pipeline cached, so what a supervisor verifies is the submission
        itself.
        """
        finding = panel.current_finding
        if finding is None:
            return

        dataset_path = self.session.dataset_path or self.dataset_path
        panel.set_source_loading()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            bundle = self.evidence_service.bundle_for(finding, dataset_path)
        except SourceUnavailable as exc:
            panel.set_source_text(
                f"SOURCE RECORDS UNAVAILABLE\n{'─' * 46}\n{exc}\n\n"
                "This does not mean the finding lacks evidence — the "
                "evidence recorded by the rule is shown above. It means "
                "the original submission could not be re-read from disk."
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        panel.set_source_text(self.format_source_bundle(bundle))

    @staticmethod
    def format_source_bundle(bundle: dict) -> str:
        grain = bundle.get("_grain", "record")
        lines = [
            f"SOURCE RECORDS  (this finding is at {grain} grain)",
            "─" * 46,
            "Rows as supplied in the submission.",
            "",
        ]

        for name, value in bundle.items():
            if name == "_grain":
                continue

            heading = name.replace("_", " ").upper()

            if isinstance(value, list):
                lines.append(f"{heading}  ({len(value)} row(s))")
                if not value:
                    lines.append("  none recorded")
                for row in value:
                    lines.append("  " + json.dumps(row, default=str))
            elif isinstance(value, dict):
                lines.append(heading)
                lines.append("  " + json.dumps(value, indent=2, default=str)
                              .replace("\n", "\n  "))
            else:
                lines.append(f"{heading}: {value}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Narration lookup
    # ------------------------------------------------------------------

    @staticmethod
    def narration_key(record):
        """Identity shared between a finding and its review-queue row."""
        return (
            str(record.get("soc_id")),
            str(record.get("finding_type")),
            str(record.get("rule_id")),
            str(record.get("alert_id")),
            str(record.get("case_id")),
        )

    def narration_for(self, finding):
        """
        The explanation for this finding, if one was generated.

        Findings and review-queue rows are separate objects describing
        the same underlying finding, so they are matched on identity
        rather than by mutating one from the other. Returns None when the
        finding was outside the explanation scope, which is the normal
        case for most findings.
        """
        wanted = self.narration_key(finding)
        for record in self.review_queue:
            if record.get("narration_source") in (None, "rule"):
                continue
            if self.narration_key(record) == wanted:
                return record
        return None

    # ============================================================
    # REVIEW QUEUE
    # ============================================================

    def build_review_page(self):
        """
        The supervisory review queue, grouped into cases.

        The engine correlates findings that share an alert into a case,
        publishing case_rank, case_priority and case_finding_count for
        exactly that purpose. Rendering the queue flat discarded it: on
        the current data 81 findings correlate into 25 cases, and the
        worst is a single alert acknowledged without investigation,
        closed with templated notes, left with no evidence record, and
        triaged late. Four rows in a flat list; one supervisory question
        when grouped.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 25, 30, 25)

        title = QLabel("Supervisory Review Queue")
        title.setObjectName("page_title")
        layout.addWidget(title)

        description = QLabel(
            "Where to spend manual review effort first. Findings that "
            "share an alert are grouped into one case, ranked by the sum "
            "of their priorities — the ranking is arithmetic and is shown "
            "in full for every case."
        )
        description.setObjectName("page_description")
        description.setWordWrap(True)
        layout.addWidget(description)

        page_layout = layout

        self.review_placeholder = QLabel("")
        self.review_placeholder.setObjectName("empty_state")
        self.review_placeholder.setAlignment(Qt.AlignCenter)
        self.review_placeholder.setWordWrap(True)
        page_layout.addWidget(self.review_placeholder, 1)

        self.review_content = QWidget()
        layout = QVBoxLayout(self.review_content)
        layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(self.review_content, 1)

        # ---- Stats + filters ---------------------------------------
        self.review_stats = QLabel("")
        self.review_stats.setObjectName("caveat")
        self.review_stats.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.review_stats)

        filters = QHBoxLayout()
        filters.setSpacing(8)

        self.review_search = QLineEdit()
        self.review_search.setPlaceholderText(
            "Search entity, rule, alert, case, or rationale text…")
        self.review_search.setClearButtonEnabled(True)
        self.review_search.textChanged.connect(self.refresh_review_queue)
        filters.addWidget(self.review_search, 1)

        self.review_soc_filter = QComboBox()
        self.review_soc_filter.addItem("All Entities")
        self.review_severity_filter = QComboBox()
        self.review_severity_filter.addItems(
            ["All Severities", "CRITICAL", "HIGH", "MEDIUM", "LOW", "UNRATED"])

        for widget in (self.review_soc_filter, self.review_severity_filter):
            widget.setMinimumWidth(150)
            widget.currentIndexChanged.connect(self.refresh_review_queue)
            filters.addWidget(widget)

        self.review_compound_only = QCheckBox("Compounding cases only")
        self.review_compound_only.setToolTip(
            "Show only cases where more than one finding landed on the "
            "same alert.")
        self.review_compound_only.toggled.connect(self.refresh_review_queue)
        filters.addWidget(self.review_compound_only)

        layout.addLayout(filters)

        # ---- Cases | findings-in-case | detail ----------------------
        splitter = QSplitter(Qt.Horizontal)

        left = QSplitter(Qt.Vertical)

        self.case_table = QTableWidget()
        self.case_table.setColumnCount(7)
        self.case_table.setHorizontalHeaderLabels(
            ["Case", "Entity", "Severity", "Findings", "Case Priority",
             "Alert", "What the case is"])
        self.case_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.case_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.case_table.setSelectionMode(QTableWidget.SingleSelection)
        self.case_table.verticalHeader().setVisible(False)
        self.case_table.horizontalHeader().setStretchLastSection(True)
        self.case_table.itemSelectionChanged.connect(self.review_case_selected)
        left.addWidget(self.case_table)

        findings_host = QWidget()
        findings_layout = QVBoxLayout(findings_host)
        findings_layout.setContentsMargins(0, 6, 0, 0)
        findings_layout.setSpacing(4)

        self.case_findings_title = QLabel("Findings in the selected case")
        self.case_findings_title.setObjectName("section_title")
        findings_layout.addWidget(self.case_findings_title)

        self.review_table = QTableWidget()
        self.review_table.setColumnCount(6)
        self.review_table.setHorizontalHeaderLabels(
            ["Queue", "Severity", "Finding", "Rule", "Case", "Priority"])
        self.review_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.review_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.review_table.setSelectionMode(QTableWidget.SingleSelection)
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.horizontalHeader().setStretchLastSection(True)
        self.review_table.itemSelectionChanged.connect(self.review_selected)
        findings_layout.addWidget(self.review_table)

        left.addWidget(findings_host)
        left.setSizes([340, 260])
        splitter.addWidget(left)

        self.review_detail_panel = FindingDetailPanel()
        self.review_detail_panel.source_requested.connect(
            self.load_review_source_records)
        splitter.addWidget(self.review_detail_panel)

        splitter.setSizes([760, 620])
        layout.addWidget(splitter)

        return page

    # ------------------------------------------------------------------
    # Review queue behaviour
    # ------------------------------------------------------------------

    def refresh_review_queue(self):
        if not hasattr(self, "case_table"):
            return

        all_cases = review_service.build_cases(self.review_queue)

        current = self.review_soc_filter.currentText()
        entities = sorted({case.soc_id for case in all_cases if case.soc_id})
        self.review_soc_filter.blockSignals(True)
        self.review_soc_filter.clear()
        self.review_soc_filter.addItem("All Entities")
        self.review_soc_filter.addItems(entities)
        index = self.review_soc_filter.findText(current)
        self.review_soc_filter.setCurrentIndex(index if index >= 0 else 0)
        self.review_soc_filter.blockSignals(False)

        soc = self.review_soc_filter.currentText()
        severity = self.review_severity_filter.currentText()

        self.review_cases = review_service.filter_cases(
            all_cases,
            soc_id="" if soc == "All Entities" else soc,
            severity="" if severity == "All Severities" else severity,
            query=self.review_search.text(),
            compound_only=self.review_compound_only.isChecked(),
        )

        stats = review_service.queue_statistics(all_cases)
        self.review_stats.setText(
            f"{stats['findings']} prioritised findings correlated into "
            f"{stats['cases']} cases · {stats['compound_cases']} cases carry "
            f"more than one finding · largest case holds "
            f"{stats['largest_case']} · showing {len(self.review_cases)}"
        )

        self.case_table.setRowCount(len(self.review_cases))
        for row, case in enumerate(self.review_cases):
            cells = [
                str(case.case_rank),
                case.soc_id,
                case.worst_severity,
                f"{case.finding_count}",
                f"{case.case_priority:g}",
                case.alert_id or "—",
                case.summary,
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 2:
                    item.setForeground(QColor(severity_color(case.worst_severity)))
                if column == 3 and case.finding_count > 1:
                    item.setForeground(QColor("#e8c07d"))
                self.case_table.setItem(row, column, item)
            self.case_table.item(row, 0).setData(Qt.UserRole, row)

        self.case_table.resizeColumnsToContents()

        if self.review_cases:
            if not self.case_table.selectedItems():
                self.case_table.selectRow(0)
            else:
                self.review_case_selected()
        else:
            self.review_table.setRowCount(0)
            self.case_findings_title.setText("No cases match the filters")
            self.review_detail_panel.clear("No cases match the filters")

    def selected_review_case(self):
        items = self.case_table.selectedItems()
        if not items:
            return None
        item = self.case_table.item(items[0].row(), 0)
        index = item.data(Qt.UserRole) if item else None
        if index is None or index >= len(self.review_cases):
            return None
        return self.review_cases[index]

    def review_case_selected(self):
        case = self.selected_review_case()
        if case is None:
            return

        self.case_findings_title.setText(
            f"Findings in case {case.case_rank} — {case.finding_count} "
            f"finding(s) on alert {case.alert_id or '—'}"
            if case.alert_id else
            f"Findings in case {case.case_rank} — entity-level")

        self.review_table.setRowCount(len(case.findings))
        for row, finding in enumerate(case.findings):
            severity = review_service.severity_of(finding)
            cells = [
                str(finding.get("queue_rank", "")),
                severity,
                str(finding.get("finding_type", "")),
                str(finding.get("rule_id", "")),
                str(finding.get("case_id") or "—"),
                f"{float(finding.get('queue_priority', 0)):g}",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setForeground(QColor(severity_color(severity)))
                self.review_table.setItem(row, column, item)
            self.review_table.item(row, 0).setData(Qt.UserRole, finding)

        self.review_table.resizeColumnsToContents()
        self.review_table.selectRow(0)

    def review_selected(self):
        rows = self.review_table.selectedItems()
        if not rows:
            return
        item = self.review_table.item(rows[0].row(), 0)
        finding = item.data(Qt.UserRole) if item else None
        if finding is None:
            return

        case = self.selected_review_case()
        extra = None
        if case is not None:
            extra = ("WHY THIS CASE IS PRIORITISED", case.why_prioritised())

        self.review_detail_panel.show_finding(
            finding, self.assessment_config(),
            narration=self.narration_for(finding),
            extra_section=extra)

    def load_review_source_records(self):
        self.load_source_into(self.review_detail_panel)

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

        page_layout = layout
        self.reports_placeholder = QLabel("")
        self.reports_placeholder.setObjectName("empty_state")
        self.reports_placeholder.setAlignment(Qt.AlignCenter)
        self.reports_placeholder.setWordWrap(True)
        page_layout.addWidget(self.reports_placeholder, 1)

        self.reports_content = QWidget()
        layout = QVBoxLayout(self.reports_content)
        layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(self.reports_content, 1)

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

        results = self.current_result

        # ---- Executive overview -----------------------------------
        summary = dashboard_service.overview(results, self.review_queue)

        for card, value in (
            (self.kpi_socs, summary.entities),
            (self.kpi_findings, summary.total_findings),
            (self.kpi_execution, summary.execution_gaps),
            (self.kpi_negative, summary.negative_space),
            (self.kpi_critical, summary.critical),
            (self.kpi_high, summary.high),
            (self.kpi_anomalies, summary.anomalies),
            (self.kpi_review, summary.review_queue),
        ):
            card.value_label.setText(f"{value:,}")

        # Both numbers, always. An explained slice is a deliberately
        # small fraction of a much larger deterministic result, and the
        # dashboard must not let it read as the whole assessment.
        self.kpi_explained.value_label.setText(
            f"{summary.explained} / {summary.total_findings:,}")

        self.dashboard_status.setText(
            f"{summary.entities} entities assessed over "
            f"{summary.total_alerts:,} alerts. "
            f"{summary.total_findings:,} findings, "
            f"{summary.review_queue} prioritised for review."
        )

        # ---- WHO ---------------------------------------------------
        self.dashboard_rows = dashboard_service.entity_rankings(results)
        self.risk_table.setRowCount(len(self.dashboard_rows))

        for index, row in enumerate(self.dashboard_rows):
            cells = [
                str(row.rank),
                row.soc_id,
                row.organization,
                row.peer_group,
                f"{row.risk_score:.2f}",
                row.percentile_label,
                row.deviation_label,
                f"{row.critical:,}",
                f"{row.high:,}",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 7 and row.critical:
                    item.setForeground(QColor(severity_color("CRITICAL")))
                elif column == 8 and row.high:
                    item.setForeground(QColor(severity_color("HIGH")))
                elif column == 6 and not row.comparable:
                    item.setForeground(QColor("#7d8899"))
                self.risk_table.setItem(index, column, item)

            self.risk_table.item(index, 0).setData(Qt.UserRole, row.soc_id)

        self.risk_table.resizeColumnsToContents()
        self.peer_note.setText(
            dashboard_service.peer_comparison_note(self.dashboard_rows))

        # ---- WHAT TO REVIEW ----------------------------------------
        self.refresh_review_preview()

        # ---- Trends -------------------------------------------------
        self.refresh_trends()

        # ---- WHY + WHAT EVIDENCE ------------------------------------
        # Default to the highest-risk entity: the page should open on
        # the entity a supervisor is most likely to want.
        if self.dashboard_rows and not self.risk_table.selectedItems():
            self.risk_table.selectRow(0)
        else:
            self.dashboard_entity_changed()

    def refresh_trends(self):
        """
        Show trends only when more than one period was assessed.

        With a single period the panel stays hidden and the note says
        what would make trends available. A line drawn through one point
        would be fabrication.
        """
        report = getattr(self, "trend_report", None)

        if report is None or not report.available:
            self.trend_panel.hide()
            message = (report.message if report is not None
                       else dashboard_service.trend_status(
                           self.current_result).message)
            self.trend_note.setText(message)
            return

        self.trend_note.setText(report.summary())
        self.trend_panel.show()

        rank_by_soc = {r.soc_id: r for r in report.rank_changes}
        rows = sorted(report.risk_score)
        self.trend_table.setRowCount(len(rows))

        for index, soc_id in enumerate(rows):
            series = report.risk_score[soc_id]
            findings = report.total_findings.get(soc_id)
            rank = rank_by_soc.get(soc_id)

            cells = [
                soc_id,
                f"{series.first:,.1f}" if series.first is not None else "—",
                f"{series.last:,.1f}" if series.last is not None else "—",
                series.change_label(),
                series.direction,
                rank.label if rank else "—",
                (findings.change_label() if findings else "—"),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 4:
                    item.setForeground(QColor(self.TREND_COLOURS.get(
                        series.direction, "#8c9ab0")))
                self.trend_table.setItem(index, column, item)
            self.trend_table.item(index, 0).setData(Qt.UserRole, soc_id)

        self.trend_table.resizeColumnsToContents()

        persistent = trends.persistent_findings(report)
        if persistent:
            worst = persistent[:3]
            names = ", ".join(
                f"{p.soc_id} {p.finding_type.replace('_', ' ').lower()}"
                for p in worst)
            self.persistent_note.setText(
                f"{len(persistent)} finding types recurred in every one of "
                f"the {len(report.periods)} periods — conditions that were "
                f"never resolved. Largest: {names}."
            )
        else:
            self.persistent_note.setText(
                "No finding type recurred across every period.")

        self.update_trend_chart()

    def update_trend_chart(self):
        """The trend chart follows the entity selected in the ranking."""
        report = getattr(self, "trend_report", None)
        if report is None or not report.available:
            return

        soc_id = self.selected_dashboard_entity()
        series = report.risk_score.get(soc_id)
        if series is None:
            self.trend_chart.set_data([], [])
            return

        self.trend_chart.set_data(series.periods, series.values, soc_id)

    def selected_dashboard_entity(self):
        items = self.risk_table.selectedItems()
        if not items:
            return None
        item = self.risk_table.item(items[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    def dashboard_entity_changed(self):
        """Update the WHY and WHAT EVIDENCE panels for the selected entity."""
        if not self.current_result:
            return

        soc_id = self.selected_dashboard_entity()
        results = self.current_result

        # Six, not eight: the seventh and eighth contributors are
        # rounding on every entity in the sample, and fewer rows leaves
        # each category label room to render without truncation.
        drivers = (dashboard_service.risk_drivers(results, soc_id, limit=6)
                   if soc_id else [])

        if drivers:
            self.driver_chart.set_data(
                [d.finding_type.replace("_", " ").title() for d in drivers],
                [d.contribution for d in drivers],
            )
            top = drivers[0]
            self.driver_note.setText(
                f"{soc_id}: {top.finding_type.replace('_', ' ')} contributes "
                f"{top.share:.0%} of the score — {top.raw_count:,} findings, "
                f"{top.normalized_per_100:.2f} per 100 alerts, "
                f"x weight {top.weight:g} = {top.contribution:.2f}. "
                f"Every contribution is recomputable by hand from the "
                f"finding counts and the configured weights."
            )
        else:
            self.driver_chart.set_data([], [])
            self.driver_note.setText("")

        title = self.driver_title.findChild(QLabel)
        if title is not None:
            title.setText(f"Risk Drivers & Evidence — {soc_id}"
                          if soc_id else "Risk Drivers & Evidence")

        self.update_trend_chart()

        severity = dashboard_service.severity_distribution(results, soc_id)
        self.severity_chart.set_data(severity.labels, severity.values)

        category = dashboard_service.category_distribution(results, soc_id)
        self.category_chart.set_data(category.labels, category.values)

    def refresh_review_preview(self):
        preview = dashboard_service.review_preview(self.review_queue, limit=10)
        self.review_preview_table.setRowCount(len(preview))

        for index, record in enumerate(preview):
            severity = str(record.get("severity") or "UNRATED")
            reason = (
                f"weight {record.get('finding_weight', 0):g} x severity "
                f"{record.get('severity_boost', 0):g} = "
                f"{record.get('queue_priority', 0):g}"
            )
            cells = [
                str(record.get("queue_rank", "")),
                str(record.get("soc_id", "")),
                severity,
                str(record.get("finding_type", "")).replace("_", " ").title(),
                str(record.get("rule_id", "")),
                reason,
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 2:
                    item.setForeground(QColor(severity_color(severity)))
                self.review_preview_table.setItem(index, column, item)

        self.review_preview_table.resizeColumnsToContents()

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
            QLabel#ai_status {
                padding: 7px 10px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
                background-color: #1b2431;
                color: #93a4bd;
                border-left: 3px solid #3d4b5f;
            }
            QLabel#ai_status[state="available"] {
                color: #7fd1a0;
                border-left: 3px solid #2e7d52;
            }
            QLabel#ai_status[state="mock"] {
                color: #9fb8e0;
                border-left: 3px solid #4a6fa5;
            }
            QLabel#ai_status[state="unavailable"] {
                color: #e8c07d;
                border-left: 3px solid #a8792c;
            }
            QLabel#ai_status[state="not_configured"] {
                color: #93a4bd;
                border-left: 3px solid #3d4b5f;
            }
            QLabel#ai_status[state="disabled"] {
                color: #7d8899;
                border-left: 3px solid #3d4b5f;
            }
            /* Scroll areas default to a warm neutral #323232 viewport,
               which reads as a brown panel sitting on the cool blue-black
               page. Both the viewport and the widget inside it have to be
               cleared, not just the frame. */
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollArea > QWidget > QScrollBar {
                background: #131a24;
            }
            QScrollBar:vertical {
                background: #0f151d;
                width: 11px;
                margin: 0;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #2b3648;
                min-height: 28px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover { background: #3b4a63; }
            QScrollBar:horizontal {
                background: #0f151d;
                height: 11px;
                margin: 0;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background: #2b3648;
                min-width: 28px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover { background: #3b4a63; }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0; height: 0; border: none; background: none;
            }
            QScrollBar::add-page, QScrollBar::sub-page { background: none; }
            QLabel#section_subtitle {
                color: #7d8899;
                font-size: 11px;
            }
            QLabel#caveat {
                color: #8c9ab0;
                font-size: 11px;
                padding: 4px 2px;
            }
            QFrame#panel {
                background: #182231;
                border: 1px solid #222d3b;
                border-radius: 8px;
            }
            QTextEdit#deterministic_detail {
                background-color: #141b26;
                border: 1px solid #2b3648;
                border-left: 3px solid #4a6fa5;
                border-radius: 4px;
                font-family: monospace;
                font-size: 11px;
                padding: 8px;
            }
            QTextEdit#source_detail {
                background-color: #10161f;
                border: 1px solid #222d3b;
                border-radius: 4px;
                font-family: monospace;
                font-size: 10px;
                padding: 8px;
                color: #a9b6c9;
            }
            /* Visually distinct from the authoritative panels above:
               dashed border and a warm accent so a supplementary
               explanation can never be mistaken for the record. */
            QFrame#ai_panel {
                background-color: #1c1a17;
                border: 1px dashed #6b5a3a;
                border-radius: 6px;
                padding: 6px;
            }
            QLabel#ai_panel_header {
                font-weight: 700;
                font-size: 11px;
                color: #e8c07d;
                padding: 2px;
            }
            QLabel#ai_panel_header[state="mock"] { color: #9fb8e0; }
            QTextEdit#ai_panel_text {
                background-color: #15140f;
                border: none;
                font-size: 11px;
                color: #cfc6b4;
            }
            QLabel#empty_state {
                color: #7d8899;
                font-size: 13px;
                line-height: 150%;
                padding: 40px;
            }
            QStatusBar {
                background-color: #131a24;
                border-top: 1px solid #2b3648;
            }
            QStatusBar::item { border: none; }
            QLabel#status_item {
                color: #8c9ab0;
                font-size: 11px;
                padding: 2px 6px;
            }
            QLabel#status_offline {
                color: #7fd1a0;
                font-size: 10px;
                font-weight: 700;
                padding: 2px 10px;
            }
            QMenuBar {
                background-color: #131a24;
                color: #cfd8e6;
                border-bottom: 1px solid #2b3648;
            }
            QMenuBar::item:selected { background-color: #23324a; }
            QMenu {
                background-color: #182231;
                color: #cfd8e6;
                border: 1px solid #2b3648;
            }
            QMenu::item:selected { background-color: #23324a; }
            QMenu::item:disabled { color: #5a6474; }
            QLabel#settings_note {
                color: #8c9ab0;
                font-size: 11px;
            }
            QLabel#settings_hint {
                color: #8c9ab0;
                font-size: 11px;
            }
            QGroupBox {
                border: 1px solid #2b3648;
                border-radius: 5px;
                margin-top: 14px;
                padding: 14px 12px 12px 12px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #cfd8e6;
            }
            QProgressBar#narration_progress {
                border: 1px solid #2b3648;
                border-radius: 4px;
                background-color: #141b26;
                height: 20px;
                text-align: center;
                color: #cfd8e6;
                font-size: 11px;
            }
            QProgressBar#narration_progress::chunk {
                background-color: #4a6fa5;
                border-radius: 3px;
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