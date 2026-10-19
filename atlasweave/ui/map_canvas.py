"""Interactive world canvas for the M2 navigation batch."""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget


class MapCanvas(QWidget):
    """Render a project world and provide deterministic navigation primitives.

    The canvas keeps navigation state in screen pixels and derives the world
    transform from the project dimensions.  It intentionally does not own
    domain objects or create regions; drawing commands are introduced in M3.
    """

    coordinate_changed = pyqtSignal(float, float, bool)
    zoom_changed = pyqtSignal(float)
    selection_changed = pyqtSignal(object)

    MIN_ZOOM_PERCENT = 5.0
    MAX_ZOOM_PERCENT = 6400.0
    VIEW_MARGIN = 40.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mapCanvas")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._world_width = 1200.0
        self._world_height = 800.0
        self._background = QColor("#FFFFFF")
        self._outside = QColor("#DDE2E7")
        self._zoom_percent = 100.0
        self._pan = QPointF()
        self._pan_mode = False
        self._dragging = False
        self._drag_last = QPointF()
        self._regions: dict[str, dict] = {}
        self._selected_ids: set[str] = set()
        self._selection_origin: QPointF | None = None
        self._selection_rect: QRectF | None = None
        self._selecting = False

    @property
    def world_size(self) -> tuple[float, float]:
        return self._world_width, self._world_height

    @property
    def zoom_percent(self) -> float:
        return self._zoom_percent

    @property
    def pan_offset(self) -> QPointF:
        return QPointF(self._pan)

    @property
    def pan_mode(self) -> bool:
        return self._pan_mode

    def set_world(
        self,
        width_km: float,
        height_km: float,
        map_style: Mapping | None = None,
    ) -> None:
        self._world_width = float(width_km)
        self._world_height = float(height_km)
        if map_style:
            self._background = QColor(str(map_style.get("background_color", "#FFFFFF")))
            self._outside = QColor(str(map_style.get("outside_color", "#DDE2E7")))
        self.fit_world()

    def set_pan_mode(self, enabled: bool) -> None:
        self._pan_mode = bool(enabled)
        self._dragging = False
        self.setCursor(
            Qt.CursorShape.OpenHandCursor if self._pan_mode else Qt.CursorShape.ArrowCursor
        )
        self.update()

    @property
    def selected_region_ids(self) -> frozenset[str]:
        return frozenset(self._selected_ids)

    @property
    def selected_area_km2(self) -> float:
        return sum(
            self._regions[region_id]["area"]
            for region_id in self._selected_ids
            if region_id in self._regions
        )

    def set_regions(self, regions: Mapping, topology) -> None:
        """Load real region faces for rendering and hit testing."""
        records: dict[str, dict] = {}
        for region_id, region in regions.items():
            if not region.visible:
                records[region_id] = {"region": region, "paths": (), "area": 0.0}
                continue
            paths = []
            area = 0.0
            try:
                for face_id in region.face_ids:
                    polygon = topology.face_polygon(face_id)
                    outer = topology.ring_points(topology.faces[face_id].outer_ring_id)
                    path = QPainterPath()
                    path.setFillRule(Qt.FillRule.OddEvenFill)
                    path.addPolygon(QPolygonF([QPointF(p.x, p.y) for p in outer]))
                    for ring_id in topology.faces[face_id].inner_ring_ids:
                        hole = topology.ring_points(ring_id)
                        path.addPolygon(QPolygonF([QPointF(p.x, p.y) for p in hole]))
                    paths.append(path)
                    area += float(polygon.area)
            except (KeyError, ValueError, TypeError):
                paths = []
                area = 0.0
            records[region_id] = {"region": region, "paths": tuple(paths), "area": area}
        self._regions = records
        self._selected_ids.intersection_update(records)
        self.selection_changed.emit(tuple(sorted(self._selected_ids)))
        self.update()

    def clear_regions(self) -> None:
        self._regions = {}
        self.clear_selection()

    def set_selection(self, region_ids, *, additive: bool = False) -> None:
        ids = {region_id for region_id in region_ids if region_id in self._regions}
        if additive:
            ids ^= self._selected_ids
        if ids == self._selected_ids:
            return
        self._selected_ids = ids
        self.selection_changed.emit(tuple(sorted(ids)))
        self.update()

    def clear_selection(self) -> None:
        self.set_selection(())

    def fit_world(self) -> None:
        self._zoom_percent = 100.0
        self._pan = QPointF()
        self._clamp_pan()
        self.zoom_changed.emit(self._zoom_percent)
        self.update()

    def set_zoom_percent(self, value: float, anchor: QPointF | None = None) -> None:
        target = max(self.MIN_ZOOM_PERCENT, min(self.MAX_ZOOM_PERCENT, float(value)))
        if abs(target - self._zoom_percent) < 1e-9:
            return
        if anchor is None:
            anchor = QPointF(self.rect().center())
        world = self.screen_to_world(anchor)
        self._zoom_percent = target
        self._pan = self._pan_for_anchor(world, anchor)
        self._clamp_pan()
        self.zoom_changed.emit(self._zoom_percent)
        self.update()

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        self.set_zoom_percent(self._zoom_percent * float(factor), anchor)

    def world_to_screen(self, point: QPointF) -> QPointF:
        scale = self._scale()
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        world_center = QPointF(self._world_width / 2.0, self._world_height / 2.0)
        return center + self._pan + (point - world_center) * scale

    def screen_to_world(self, point: QPointF) -> QPointF:
        scale = self._scale()
        if scale <= 0:
            return QPointF()
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        world_center = QPointF(self._world_width / 2.0, self._world_height / 2.0)
        return world_center + (point - center - self._pan) / scale

    def world_rect_on_screen(self) -> QRectF:
        top_left = self.world_to_screen(QPointF(0.0, 0.0))
        bottom_right = self.world_to_screen(QPointF(self._world_width, self._world_height))
        return QRectF(top_left, bottom_right).normalized()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._clamp_pan()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._outside)
        world_rect = self.world_rect_on_screen()
        painter.fillRect(world_rect, self._background)
        painter.setPen(QPen(QColor("#6B7280"), 1.0))
        painter.drawRect(world_rect)

        for region_id, record in self._regions.items():
            if not record["paths"]:
                continue
            selected = region_id in self._selected_ids
            painter.setBrush(QBrush(QColor("#1769E0" if selected else "#E8EEF5")))
            painter.setPen(
                QPen(QColor("#1769E0" if selected else "#8A99A8"), 2.0 if selected else 1.0)
            )
            for path in record["paths"]:
                screen_path = self._screen_path(path)
                painter.drawPath(screen_path)

        if not self._regions and self._world_width > 0 and self._world_height > 0:
            message = "还没有疆界"
            hint = "完成 M3 区域绘制后，可从这里开始创建第一个顶级区域"
            font = QFont(self.font())
            font.setPointSize(max(10, font.pointSize() + 1))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#374151"))
            center = world_rect.center()
            painter.drawText(
                QRectF(center.x() - 180, center.y() - 28, 360, 32),
                Qt.AlignmentFlag.AlignCenter,
                message,
            )
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor("#6B7280"))
            painter.drawText(
                QRectF(center.x() - 240, center.y() + 8, 480, 28),
                Qt.AlignmentFlag.AlignCenter,
                hint,
            )
        if self._selection_rect is not None:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#1769E0"), 1.0, Qt.PenStyle.DashLine))
            painter.drawRect(self._selection_rect.normalized())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        point = event.position()
        if self._selection_origin is not None and not self._dragging:
            if (point - self._selection_origin).manhattanLength() >= 4:
                self._selecting = True
                self._selection_rect = QRectF(self._selection_origin, point)
                self.update()
        if self._dragging:
            delta = point - self._drag_last
            self._pan += delta
            self._drag_last = point
            self._clamp_pan()
            self.update()
        self._emit_coordinate(point)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        should_pan = (
            event.button() == Qt.MouseButton.MiddleButton
            or self._pan_mode
            or bool(event.modifiers() & Qt.KeyboardModifier.Shift)
        )
        if should_pan and event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
        ):
            self._dragging = True
            self._drag_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setFocus()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._selection_origin = QPointF(event.position())
            self._selection_rect = QRectF(self._selection_origin, self._selection_origin)
            self._selecting = False
            self.setFocus()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._dragging:
            self._dragging = False
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self._pan_mode else Qt.CursorShape.ArrowCursor
            )
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._selection_origin is not None:
            end = QPointF(event.position())
            rect = QRectF(self._selection_origin, end).normalized()
            modifiers = event.modifiers()
            if self._selecting and rect.width() >= 4 and rect.height() >= 4:
                candidates = self._regions_in_rect(rect, end.x() >= self._selection_origin.x())
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    self.set_selection(candidates, additive=True)
                else:
                    self.set_selection(candidates)
            else:
                hit = self._hit_test(end)
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    self.set_selection([hit], additive=True) if hit else None
                else:
                    self.set_selection([hit] if hit else ())
            self._selection_origin = None
            self._selection_rect = None
            self._selecting = False
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(1.15**steps, event.position())
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.set_pan_mode(True)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._selected_ids:
                self.clear_selection()
            else:
                self.fit_world()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.set_pan_mode(False)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _scale(self) -> float:
        available_width = max(1.0, self.width() - self.VIEW_MARGIN * 2)
        available_height = max(1.0, self.height() - self.VIEW_MARGIN * 2)
        fit = min(available_width / self._world_width, available_height / self._world_height)
        return fit * (self._zoom_percent / 100.0)

    def _pan_for_anchor(self, world: QPointF, anchor: QPointF) -> QPointF:
        scale = self._scale()
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        world_center = QPointF(self._world_width / 2.0, self._world_height / 2.0)
        return anchor - center - (world - world_center) * scale

    def _clamp_pan(self) -> None:
        scale = self._scale()
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        half_width = self._world_width * scale / 2.0
        half_height = self._world_height * scale / 2.0
        min_x = self.VIEW_MARGIN - center_x - half_width
        max_x = self.width() - self.VIEW_MARGIN - center_x + half_width
        min_y = self.VIEW_MARGIN - center_y - half_height
        max_y = self.height() - self.VIEW_MARGIN - center_y + half_height
        self._pan.setX(max(min_x, min(max_x, self._pan.x())))
        self._pan.setY(max(min_y, min(max_y, self._pan.y())))

    def _emit_coordinate(self, point: QPointF) -> None:
        world = self.screen_to_world(point)
        rect = QRectF(0.0, 0.0, self._world_width, self._world_height)
        inside = rect.contains(world)
        self.coordinate_changed.emit(world.x(), world.y(), inside)

    def _screen_path(self, path: QPainterPath) -> QPainterPath:
        result = QPainterPath()
        for polygon in path.toFillPolygons():
            result.addPolygon(QPolygonF([self.world_to_screen(point) for point in polygon]))
        result.setFillRule(path.fillRule())
        return result

    def _hit_test(self, point: QPointF) -> str | None:
        for region_id, record in reversed(tuple(self._regions.items())):
            if not record["region"].visible:
                continue
            if any(self._screen_path(path).contains(point) for path in record["paths"]):
                return region_id
        return None

    def _regions_in_rect(self, rect: QRectF, contained: bool) -> list[str]:
        result = []
        area_path = QPainterPath()
        area_path.addRect(rect)
        for region_id, record in self._regions.items():
            if not record["region"].visible:
                continue
            paths = [self._screen_path(path) for path in record["paths"]]
            if contained:
                if paths and all(rect.contains(path.boundingRect()) for path in paths):
                    result.append(region_id)
            elif any(area_path.intersects(path) for path in paths):
                result.append(region_id)
        return result
