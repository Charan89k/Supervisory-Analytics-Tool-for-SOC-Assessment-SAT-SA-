"""
Application icon, drawn at runtime.

Generated rather than shipped as a binary asset so the repository stays
text-only and the icon renders at whatever size the platform asks for,
including under the offscreen platform plugin used by the smoke test.

Phase 17 (packaging) should replace this with a real multi-resolution
.ico / .icns / .png set — a window icon and an installer icon are not
the same artefact, and a packaged application wants a proper one.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap

#: Deep supervisory blue, matching the application's sidebar.
BACKGROUND = QColor("#1f3a5f")
FOREGROUND = QColor("#e8eef7")
ACCENT = QColor("#4a6fa5")

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    radius = size * 0.18
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(BACKGROUND))
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # A magnifier over a baseline: examination of a record, not monitoring
    # of a network. Drawn as simple geometry so it stays legible at 16px.
    painter.setBrush(Qt.NoBrush)
    pen = painter.pen()
    pen.setColor(ACCENT)
    pen.setWidthF(max(1.0, size * 0.06))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawLine(int(size * 0.20), int(size * 0.78),
                      int(size * 0.80), int(size * 0.78))

    if size >= 24:
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * 0.42))
        painter.setFont(font)
        painter.setPen(FOREGROUND)
        painter.drawText(QRectF(0, -size * 0.06, size, size),
                          Qt.AlignCenter, "SA")

    painter.end()
    return pixmap


def application_icon() -> QIcon:
    """A QIcon carrying every size a desktop environment may ask for."""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(_render(size))
    return icon
