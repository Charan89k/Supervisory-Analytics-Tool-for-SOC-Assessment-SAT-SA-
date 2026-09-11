"""
SAT-SA desktop application entry point.

This is the product. There is no server, no browser, and no network
dependency: the window is the application.
"""

import sys

from PySide6.QtWidgets import QApplication

from application.ui import MainWindow
from application.version import (
    APP_NAME,
    APP_ORGANIZATION,
    APP_VERSION,
)
from application.widgets.app_icon import application_icon


def main():
    # Answered before Qt starts: the self-check must be able to report a
    # broken install, and constructing a QApplication is one of the
    # things that could be broken.
    if "--self-check" in sys.argv[1:]:
        from application.self_check import run_self_check
        sys.exit(run_self_check())

    app = QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORGANIZATION)
    # Set on the application as well as the window so the icon is used
    # by the task switcher and launcher, not only the title bar.
    app.setWindowIcon(application_icon())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
