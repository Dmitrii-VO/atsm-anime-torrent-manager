"""Иконки рисуются в рантайме — так в репозитории нет бинарных файлов."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap, QPolygonF


def _star(painter: QPainter, center: QPointF, radius: float, color: QColor) -> None:
    import math

    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else radius * 0.45
        points.append(
            QPointF(center.x() + r * math.cos(angle), center.y() - r * math.sin(angle))
        )
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(QPolygonF(points))


def app_pixmap(badge: int = 0, size: int = 64) -> QPixmap:
    """Значок приложения. badge — счётчик новых серий в трее (ТЗ §12)."""
    scale = size / 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setBrush(QColor("#5b3fe0"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(
        QRect(int(2 * scale), int(2 * scale), int(size - 4 * scale), int(size - 4 * scale)),
        int(14 * scale),
        int(14 * scale),
    )
    _star(painter, QPointF(size / 2, size / 2), size * 0.30, QColor("#ffd76e"))

    if badge > 0:
        badge_rect = QRect(int(size - 30 * scale), int(2 * scale), int(28 * scale), int(28 * scale))
        painter.setBrush(QColor("#e5484d"))
        painter.drawEllipse(badge_rect)
        font = QFont()
        font.setPointSize(int((12 if badge < 10 else 10) * scale))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            badge_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            "99+" if badge > 99 else str(badge),
        )

    painter.end()
    return pixmap


def app_icon(badge: int = 0) -> QIcon:
    return QIcon(app_pixmap(badge))
