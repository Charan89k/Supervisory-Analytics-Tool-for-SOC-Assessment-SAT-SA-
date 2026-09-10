import sys
from PySide6.QtWidgets import QApplication

from application.ui import MainWindow


def main():
    app = QApplication(sys.argv)

    app.setApplicationName("SAT-SA")
    app.setOrganizationName("SAT-SA")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()