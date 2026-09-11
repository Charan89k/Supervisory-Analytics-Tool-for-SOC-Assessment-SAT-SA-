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


def _use_utf8_console() -> None:
    """
    Make the console able to print the characters this tool uses.

    Windows defaults a console to a legacy code page — cp1252 here —
    which cannot encode the tick the pipeline prints after each step,
    so `python main.py` died with a UnicodeEncodeError on Windows
    before it had assessed anything. POSIX terminals are already UTF-8
    and are unaffected.

    Applied at the entry points only. Nothing in analytics/ writes to a
    console, so library use is untouched.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # A redirected, wrapped or absent stream. Printing is not
            # worth failing a run over.
            pass


def main():
    _use_utf8_console()
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
