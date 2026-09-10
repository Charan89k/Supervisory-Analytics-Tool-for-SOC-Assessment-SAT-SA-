"""
Chart widgets for the supervisory dashboard.

Built on QtCharts (already present in PySide6-Addons): native, offline,
and rendered in the window. Nothing here reaches a browser or a network.

Colour decisions, and why they are what they are:

* **Severity is not colour-encoded.** The obvious design — four bars in
  red/orange/yellow/green — fails on the HIGH-vs-MEDIUM pair: orange and
  yellow are adjacent hues, and every candidate ramp measured a
  normal-vision Delta E of 10.6-13.6 against a >=15 floor. That is a pair
  full-colour readers struggle to separate, and direct labels do not
  excuse it. Severity is already named on the category axis, so colour
  was carrying no information; the bars share one hue and the axis does
  the work.
* **Two-category splits use categorical slots 1 and 2** (blue, orange),
  which validate cleanly against this application's surface: CVD Delta E
  26.8, normal-vision 31.8, both well clear of their floors.
* **Status colours appear only as badges** — one at a time, beside a
  text label that carries the meaning. A status colour never has to be
  told apart from another status colour at a glance.

Every palette here was checked with the data-viz validator against the
application's own dark surface (#182231), not a reference surface.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCharts import (
    QAbstractBarSeries,
    QBarCategoryAxis,
    QBarSet,
    QChart,
    QChartView,
    QHorizontalBarSeries,
    QValueAxis,
)
from PySide6.QtCore import QMargins, Qt
from PySide6.QtGui import QColor, QFont, QPainter

# -- surface and ink -------------------------------------------------
SURFACE = QColor("#182231")
INK_PRIMARY = QColor("#cfd8e6")
INK_SECONDARY = QColor("#8c9ab0")
GRID = QColor("#25324a")

# -- validated palette ----------------------------------------------
#: Single hue for magnitude, where the axis already names each bar.
MAGNITUDE = QColor("#3987e5")
#: Categorical slots 1 and 2 — validated as a pair on this surface.
CATEGORICAL = (QColor("#3987e5"), QColor("#d95926"))

#: Minimum height per category row.
#:
#: QtCharts silently elides every category label to "..." once a row
#: drops below roughly 25px — measured, not guessed: five rows in a
#: 104px plot area elide, the same five in 134px do not. Charts
#: therefore size themselves from their data rather than trusting a
#: caller's fixed height, because the number of categories varies with
#: the entity being shown.
MIN_ROW_PX = 28
#: Title, value axis and margins, outside the plot area.
CHART_CHROME_PX = 66

#: Status colours. Used only as a badge beside a label, never as
#: adjacent fills that must be told apart from one another.
STATUS = {
    "CRITICAL": "#d03b3b",
    "HIGH": "#ec835a",
    "MEDIUM": "#fab219",
    "LOW": "#0ca30c",
    "UNRATED": "#8c9ab0",
}


def severity_color(severity) -> str:
    return STATUS.get(str(severity or "").strip().upper(), STATUS["UNRATED"])


def _style_chart(chart: QChart, title: str = "",
                  left_margin: int = 4) -> None:
    chart.setBackgroundBrush(SURFACE)
    chart.setBackgroundPen(Qt.NoPen)
    chart.setPlotAreaBackgroundVisible(False)
    # Category labels are drawn in the left margin. In a narrow panel
    # the default leaves too little room and QtCharts silently elides
    # every label to "...", which is worse than a smaller plot area.
    chart.setMargins(QMargins(left_margin, 4, 8, 4))
    chart.legend().setVisible(False)

    if title:
        chart.setTitle(title)
        chart.setTitleBrush(INK_SECONDARY)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        chart.setTitleFont(font)


def _style_axis(axis, ink=INK_SECONDARY, show_grid=False) -> None:
    """Recessive axes: the data is the subject, the frame is not."""
    axis.setLabelsColor(ink)
    axis.setGridLineVisible(show_grid)
    axis.setGridLineColor(GRID)
    axis.setLineVisible(False)
    axis.setMinorGridLineVisible(False)
    font = QFont()
    font.setPointSize(8)
    axis.setLabelsFont(font)


class HorizontalBarChart(QChartView):
    """
    Magnitude across named categories.

    Horizontal because supervisory category names are long
    (REPEATED_ALERT_WITHOUT_REMEDIATION) and horizontal bars give them a
    full line to sit on instead of rotating them 45 degrees.
    """

    def __init__(self, title: str = "", height: int = 200,
                  left_margin: int = 4):
        super().__init__()
        self._left_margin = left_margin
        self._base_height = height
        self.setRenderHint(QPainter.Antialiasing)
        # Fixed, not minimum: inside a stretching column layout a
        # minimum-only height let a five-bar chart grow to 600px, which
        # is neither readable nor what the layout intended.
        self._apply_height(0)
        self.setStyleSheet("background: transparent; border: none;")
        self._title = title
        self.set_data([], [])

    def _apply_height(self, category_count: int) -> None:
        """Tall enough that every category label renders, never shorter."""
        needed = CHART_CHROME_PX + category_count * MIN_ROW_PX
        height = max(self._base_height, needed)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def set_data(self, labels: Sequence[str], values: Sequence[float],
                  colors: Optional[Sequence[QColor]] = None) -> None:
        """
        One bar per label, single hue.

        A single QBarSet holding every value, not one set per bar: a
        series of N sets reserves N slots inside each category, so each
        visible bar is drawn at 1/N of its row and eight drivers render
        as hairlines. One set means one bar per category at full width.

        That also settles the colour question. Per-bar colour needs one
        set per bar, and the categories are already named on the axis —
        so colour would be paying the hairline cost to encode what the
        labels already say. `colors` is accepted for the rare case where
        a caller genuinely needs per-bar identity, and left unused by
        the dashboard.
        """
        chart = QChart()
        _style_chart(chart, self._title, self._left_margin)

        self._apply_height(len(labels))

        if not labels:
            self.setChart(chart)
            return

        # QtCharts draws the first category at the bottom of a
        # horizontal chart, so reverse to read largest-first top-down.
        labels = list(labels)[::-1]
        values = [float(v) for v in values][::-1]

        series = QHorizontalBarSeries()
        series.setBarWidth(0.62)   # thin marks; the gap is the spacer
        series.setLabelsVisible(True)
        series.setLabelsPosition(QAbstractBarSeries.LabelsOutsideEnd)
        series.setLabelsFormat("@value")

        bar = QBarSet("")
        bar.append(values)
        bar.setColor(MAGNITUDE)
        bar.setBorderColor(SURFACE)     # surface gap between fills
        bar.setLabelColor(INK_SECONDARY)
        label_font = QFont()
        label_font.setPointSize(8)
        bar.setLabelFont(label_font)
        series.append(bar)

        chart.addSeries(series)

        category_axis = QBarCategoryAxis()
        category_axis.append(labels)
        _style_axis(category_axis)
        chart.addAxis(category_axis, Qt.AlignLeft)
        series.attachAxis(category_axis)

        value_axis = QValueAxis()
        value_axis.setLabelFormat("%d")
        # Headroom so an outside-end value label is not clipped by the
        # plot edge.
        value_axis.setRange(0, max(max(values), 1) * 1.25)
        value_axis.setTickCount(4)
        _style_axis(value_axis, show_grid=True)
        chart.addAxis(value_axis, Qt.AlignBottom)
        series.attachAxis(value_axis)

        chart.legend().setVisible(False)
        self.setChart(chart)


def make_bar_chart(title: str = "", height: int = 200,
                    left_margin: int = 4) -> HorizontalBarChart:
    return HorizontalBarChart(title, height, left_margin)
