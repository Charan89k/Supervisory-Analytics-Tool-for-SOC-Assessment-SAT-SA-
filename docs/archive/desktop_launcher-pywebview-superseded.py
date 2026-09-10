"""
SAT-SA Desktop Launcher
------------------------
Wraps the existing Streamlit dashboard (app.py) in a native desktop
window using pywebview, so the end user never sees a browser tab or
types localhost:8501. The Streamlit app itself is untouched.

This does NOT replace app.py's logic - it starts the same Streamlit
server as a background subprocess and points a native window at it.
When the window is closed, the server is killed automatically.

Run with:
    python desktop_launcher.py

Package into a standalone executable with:
    pyinstaller --noconfirm --onefile --name SAT-SA \
        --add-data "app.py:." \
        --add-data "analytics:analytics" \
        --add-data "config:config" \
        --add-data "schemas:schemas" \
        desktop_launcher.py

(See BUILD.md for the full packaging walkthrough, including why the
resulting binary is Linux-only unless built on Windows.)
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
STREAMLIT_APP = APP_DIR / "app.py"
HOST = "127.0.0.1"
PORT = 8765          # deliberately non-default, avoids clashing with a
                      # manually-run `streamlit run app.py` on 8501
WINDOW_TITLE = "SAT-SA — Supervisory Analytics Tool for SOC Assessment"


def _port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _wait_for_server(host: str, port: int, timeout_seconds: float = 30.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _port_is_open(host, port):
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    if not STREAMLIT_APP.exists():
        print(f"ERROR: could not find {STREAMLIT_APP}. Run this from the "
              f"soc_advisory project root, or check the PyInstaller "
              f"--add-data paths if running from a packaged build.")
        return 1

    try:
        import webview  # pywebview
    except ImportError:
        print("ERROR: pywebview is not installed. Run: pip install pywebview")
        return 1

    # Launch Streamlit headlessly as a background subprocess.
    streamlit_cmd = [
        sys.executable, "-m", "streamlit", "run", str(STREAMLIT_APP),
        "--server.port", str(PORT),
        "--server.address", HOST,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType", "none",
    ]

    print("Starting SAT-SA analytics engine...")
    proc = subprocess.Popen(
        streamlit_cmd,
        cwd=str(APP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        if not _wait_for_server(HOST, PORT):
            print("ERROR: SAT-SA engine did not start within 30 seconds.")
            proc.terminate()
            return 1

        print("Opening SAT-SA window...")
        window = webview.create_window(
            WINDOW_TITLE,
            f"http://{HOST}:{PORT}",
            width=1400,
            height=900,
            min_size=(1000, 700),
        )
        webview.start()
    finally:
        # Window closed (or startup failed) - always clean up the
        # background server so it doesn't linger as an orphan process.
        print("Shutting down SAT-SA engine...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return 0


if __name__ == "__main__":
    sys.exit(main())
