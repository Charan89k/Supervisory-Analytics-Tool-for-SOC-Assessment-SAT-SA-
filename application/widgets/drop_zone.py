from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from analytics.ingestion import (
    accepts_files,
    classify_input,
    describe_supported_inputs,
    dialog_filter,
    rejection_reason,
)


class DropZone(QFrame):
    """
    Dataset selector.

    What this widget advertises comes from analytics.ingestion, never
    from a hard-coded string here. It previously claimed
    "CSV / JSON / ZIP / DATA FOLDER" and filtered for `*.zip *.csv
    *.json` while the engine could only read a directory of CSVs, so
    any user who picked a file got a raw FileNotFoundError. A widget
    must not promise a capability the engine does not have.
    """

    path_selected = Signal(str)
    path_rejected = Signal(str)

    def __init__(self):
        super().__init__()

        self.setAcceptDrops(True)
        self.setObjectName("drop_zone")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)

        self.title = QLabel("DROP DATASET HERE")
        self.title.setObjectName("drop_title")
        self.title.setAlignment(Qt.AlignCenter)

        self.description = QLabel(describe_supported_inputs())
        self.description.setObjectName("drop_description")
        self.description.setAlignment(Qt.AlignCenter)
        self.description.setWordWrap(True)

        self.or_label = QLabel("or")
        self.or_label.setObjectName("drop_or")
        self.or_label.setAlignment(Qt.AlignCenter)

        self.browse_button = QPushButton("Browse Dataset")
        self.browse_button.setObjectName("browse_button")
        self.browse_button.setCursor(Qt.PointingHandCursor)

        self.status = QLabel("No dataset selected")
        self.status.setObjectName("drop_status")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)

        layout.addWidget(self.title)
        layout.addWidget(self.description)
        layout.addSpacing(5)
        layout.addWidget(self.or_label)
        layout.addWidget(self.browse_button)
        layout.addSpacing(10)
        layout.addWidget(self.status)

        self.browse_button.clicked.connect(self.browse_dataset)

    def browse_dataset(self):
        """
        Offer exactly the input shapes the engine can read. While the
        only supported format is a directory, this opens a directory
        chooser and nothing else — an "All Files" escape hatch here
        only produces a failure two steps later.
        """
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select SAT-SA Dataset Folder",
            "",
        )

        if folder:
            self.set_dataset(folder)
            return

        if not accepts_files():
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SAT-SA Dataset",
            "",
            dialog_filter(),
        )

        if path:
            self.set_dataset(path)

    def set_dataset(self, path):
        path_obj = Path(path)

        if classify_input(str(path_obj)) is None:
            self.status.setText("Unsupported dataset — see message.")
            self.path_rejected.emit(rejection_reason(str(path_obj)))
            return

        if path_obj.is_dir():
            display_name = f"\U0001F4C1 {path_obj.name}/"
        else:
            display_name = f"\U0001F4C4 {path_obj.name}"

        self.status.setText(display_name)
        self.path_selected.emit(str(path_obj))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()

        if not urls:
            return

        path = urls[0].toLocalFile()

        if path:
            self.set_dataset(path)
            event.acceptProposedAction()
