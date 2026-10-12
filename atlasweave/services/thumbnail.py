"""Deterministic thumbnail rendering through Qt's image paint device."""

from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

from atlasweave.domain.project import Project


def render_thumbnail(project: Project, *, width: int = 640, height: int = 360) -> bytes:
    if width < 1 or height < 1 or width > 4096 or height > 4096:
        raise ValueError("缩略图尺寸必须在 1–4096 像素范围内")
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#DDE2E7"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    margin = min(width, height) * 0.06
    world = QRectF(margin, margin, width - 2 * margin, height - 2 * margin)
    painter.fillRect(world, QColor(project.map_style.get("background_color", "#FFFFFF")))
    scale = min(world.width() / project.world_width_km, world.height() / project.world_height_km)
    x_offset = world.left() + (world.width() - project.world_width_km * scale) / 2
    y_offset = world.top() + (world.height() - project.world_height_km * scale) / 2
    pen = QPen(QColor("#5E6C7A"), max(1.0, width / 640))
    painter.setPen(pen)
    for region in project.regions.values():
        color = QColor(region.fill_color or "#D9E8FB")
        painter.setBrush(color)
        for face_id in region.face_ids:
            try:
                points = project.topology.ring_points(project.topology.faces[face_id].outer_ring_id)
            except (KeyError, ValueError):
                continue
            polygon = QPolygonF(
                [
                    QPointF(
                        x_offset + point.x * scale,
                        y_offset + (project.world_height_km - point.y) * scale,
                    )
                    for point in points
                ]
            )
            painter.drawPolygon(polygon, Qt.FillRule.OddEvenFill)
    painter.setPen(QPen(QColor("#CDD4DB"), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(world)
    painter.end()
    from PyQt6.QtCore import QBuffer, QIODevice

    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(output, "PNG"):
        raise OSError("无法生成工程缩略图")
    buffer = bytes(output.data())
    output.close()
    return buffer


def write_thumbnail(project: Project, path: Path) -> None:
    path.write_bytes(render_thumbnail(project))
