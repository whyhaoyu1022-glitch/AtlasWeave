"""Interactive world canvas for the M2 navigation batch."""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget
from shapely.errors import GEOSException
from shapely.geometry import box
from shapely.ops import unary_union
from shapely.validation import explain_validity


class MapCanvas(QWidget):
    """Render a project world and provide deterministic navigation primitives.

    The canvas keeps navigation state in screen pixels and derives the world
    transform from the project dimensions. It intentionally does not own
    domain objects; commands receive completed world-coordinate drafts.
    """

    coordinate_changed = pyqtSignal(float, float, bool)
    zoom_changed = pyqtSignal(float)
    selection_changed = pyqtSignal(object)
    draft_changed = pyqtSignal(object)
    polygon_completed = pyqtSignal(object)
    freehand_changed = pyqtSignal(object)
    freehand_completed = pyqtSignal(object)
    split_changed = pyqtSignal(object)
    split_completed = pyqtSignal(object)

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
        self._geometry_errors: dict[str, str] = {}
        self._selected_ids: set[str] = set()
        self._selection_origin: QPointF | None = None
        self._selection_rect: QRectF | None = None
        self._selecting = False
        self._draw_mode = False
        self._draft_points: list[QPointF] = []
        self._draft_cursor: QPointF | None = None
        self._freehand_mode = False
        self._freehand_points: list[QPointF] = []
        self._freehand_cursor: QPointF | None = None
        self._freehand_dragging = False
        self._freehand_last_screen: QPointF | None = None
        self._split_mode = False
        self._split_points: list[QPointF] = []
        self._split_cursor: QPointF | None = None
        self._split_dragging = False
        self._split_last_screen: QPointF | None = None
        self._split_preview: tuple[tuple[QPointF, ...], ...] = ()

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
    def draw_mode(self) -> bool:
        return self._draw_mode

    @property
    def draft_points(self) -> tuple[QPointF, ...]:
        return tuple(QPointF(point) for point in self._draft_points)

    def set_draw_mode(self, enabled: bool) -> None:
        self._draw_mode = bool(enabled)
        if self._draw_mode:
            self.set_freehand_mode(False)
            self.set_split_mode(False)
        self._selection_origin = None
        self._selection_rect = None
        self._selecting = False
        self._dragging = False
        if not self._draw_mode:
            self.cancel_drawing()
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._draw_mode else Qt.CursorShape.ArrowCursor
        )
        self.update()

    @property
    def freehand_mode(self) -> bool:
        return self._freehand_mode

    @property
    def freehand_points(self) -> tuple[QPointF, ...]:
        return tuple(QPointF(point) for point in self._freehand_points)

    def set_freehand_mode(self, enabled: bool) -> None:
        self._freehand_mode = bool(enabled)
        if self._freehand_mode:
            self._draw_mode = False
            self.set_split_mode(False)
            self.cancel_drawing()
        else:
            self.cancel_freehand()
        self._selection_origin = None
        self._selection_rect = None
        self._selecting = False
        self._dragging = False
        self._freehand_dragging = False
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._freehand_mode else Qt.CursorShape.ArrowCursor
        )
        self.update()

    @property
    def split_mode(self) -> bool:
        return self._split_mode

    @property
    def split_points(self) -> tuple[QPointF, ...]:
        return tuple(QPointF(point) for point in self._split_points)

    def set_split_mode(self, enabled: bool) -> None:
        self._split_mode = bool(enabled)
        if self._split_mode:
            self._draw_mode = False
            self.set_freehand_mode(False)
            self.cancel_drawing()
            self.cancel_freehand()
        else:
            self.cancel_split()
        self._selection_origin = None
        self._selection_rect = None
        self._selecting = False
        self._dragging = False
        self._split_dragging = False
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._split_mode else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def cancel_split(self) -> None:
        if self._split_points or self._split_cursor is not None or self._split_preview:
            self._split_points.clear()
            self._split_cursor = None
            self._split_dragging = False
            self._split_last_screen = None
            self._split_preview = ()
            self.split_changed.emit(())
            self.update()

    def set_split_preview(self, polygons) -> None:
        """Render temporary result faces without changing the project model."""
        converted: list[tuple[QPointF, ...]] = []
        for polygon in polygons:
            coordinates = polygon.exterior.coords if hasattr(polygon, "exterior") else polygon
            points = tuple(QPointF(float(x), float(y)) for x, y in coordinates)
            if len(points) >= 4:
                converted.append(points)
        self._split_preview = tuple(converted)
        self.update()

    def finish_split(self) -> bool:
        if len(self._split_points) < 2:
            return False
        self._split_dragging = False
        self.split_completed.emit(self.split_points)
        return True

    def _add_split_screen_point(self, point: QPointF) -> None:
        if self._split_last_screen is not None:
            if (point - self._split_last_screen).manhattanLength() < 3:
                return
        self._split_last_screen = QPointF(point)
        world = self.screen_to_world(point)
        if self._split_points and (world - self._split_points[-1]).manhattanLength() < 1e-9:
            return
        self._split_points.append(world)
        self.split_changed.emit(self.split_points)
        self.update()

    def cancel_freehand(self) -> None:
        if self._freehand_points or self._freehand_cursor is not None:
            self._freehand_points.clear()
            self._freehand_cursor = None
            self._freehand_dragging = False
            self._freehand_last_screen = None
            self.freehand_changed.emit(())
            self.update()

    def finish_freehand(self) -> bool:
        if len(self._freehand_points) < 3:
            return False
        self._freehand_dragging = False
        self.freehand_completed.emit(self.freehand_points)
        return True

    def _add_freehand_screen_point(self, point: QPointF) -> None:
        if self._freehand_last_screen is not None:
            if (point - self._freehand_last_screen).manhattanLength() < 3:
                return
        self._freehand_last_screen = QPointF(point)
        world = self.screen_to_world(point)
        if self._freehand_points:
            previous = self._freehand_points[-1]
            if (world - previous).manhattanLength() < 1e-9:
                return
        self._freehand_points.append(world)
        self.freehand_changed.emit(self.freehand_points)
        self.update()

    def cancel_drawing(self) -> None:
        if self._draft_points or self._draft_cursor is not None:
            self._draft_points.clear()
            self._draft_cursor = None
            self.draft_changed.emit(())
            self.update()

    def remove_last_draft_point(self) -> None:
        if self._draft_points:
            self._draft_points.pop()
            self.draft_changed.emit(self.draft_points)
            self.update()

    def add_draft_point(self, point: QPointF) -> None:
        world = self.screen_to_world(point)
        self._draft_points.append(world)
        self.draft_changed.emit(self.draft_points)
        self.update()

    def finish_drawing(self) -> bool:
        if len(self._draft_points) < 3:
            return False
        self.polygon_completed.emit(self.draft_points)
        return True

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

    @property
    def geometry_errors(self) -> Mapping[str, str]:
        return dict(self._geometry_errors)

    def set_regions(self, regions: Mapping, topology) -> None:
        """Load real region faces for rendering and hit testing."""
        records: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for region_id, region in regions.items():
            if not region.visible:
                records[region_id] = {
                    "region": region,
                    "paths": (),
                    "area": 0.0,
                    "geometry": None,
                }
                continue
            paths = []
            area = 0.0
            geometries = []
            try:
                for face_id in region.face_ids:
                    polygon = topology.face_polygon(face_id)
                    if not polygon.is_valid:
                        raise ValueError(f"面几何无效：{explain_validity(polygon)}")
                    outer = topology.ring_points(topology.faces[face_id].outer_ring_id)
                    path = QPainterPath()
                    path.setFillRule(Qt.FillRule.OddEvenFill)
                    path.addPolygon(QPolygonF([QPointF(p.x, p.y) for p in outer]))
                    for ring_id in topology.faces[face_id].inner_ring_ids:
                        hole = topology.ring_points(ring_id)
                        path.addPolygon(QPolygonF([QPointF(p.x, p.y) for p in hole]))
                    paths.append(path)
                    area += float(polygon.area)
                    geometries.append(polygon)
            except (KeyError, ValueError, TypeError, GEOSException) as exc:
                paths = []
                area = 0.0
                errors[region_id] = str(exc)
            records[region_id] = {
                "region": region,
                "paths": tuple(paths),
                "area": area,
                "geometry": self._union_geometries(geometries),
            }
        self._regions = records
        self._geometry_errors = errors
        self._selected_ids.intersection_update(records)
        self.selection_changed.emit(tuple(sorted(self._selected_ids)))
        self.update()

    def clear_regions(self) -> None:
        self._regions = {}
        self._geometry_errors = {}
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

        if self._split_preview:
            preview_colors = (QColor(37, 99, 235, 70), QColor(16, 185, 129, 70))
            painter.setPen(QPen(QColor("#1D4ED8"), 2.0, Qt.PenStyle.DashLine))
            for index, polygon in enumerate(self._split_preview):
                painter.setBrush(QBrush(preview_colors[index % len(preview_colors)]))
                painter.drawPolygon(QPolygonF([self.world_to_screen(point) for point in polygon]))

        if self._draft_points:
            draft = QPolygonF([self.world_to_screen(point) for point in self._draft_points])
            painter.setBrush(QBrush(QColor(23, 105, 224, 35)))
            painter.setPen(QPen(QColor("#1769E0"), 2.0, Qt.PenStyle.DashLine))
            painter.drawPolyline(draft)
            if self._draft_cursor is not None:
                painter.drawLine(draft[-1], self.world_to_screen(self._draft_cursor))
            painter.setBrush(QBrush(QColor("#FFFFFF")))
            painter.setPen(QPen(QColor("#1769E0"), 2.0))
            for point in draft:
                painter.drawEllipse(point, 4.0, 4.0)

        if self._freehand_points:
            freehand = QPolygonF([self.world_to_screen(point) for point in self._freehand_points])
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#8B5CF6"), 2.0, Qt.PenStyle.DashLine))
            painter.drawPolyline(freehand)
            if self._freehand_cursor is not None:
                painter.drawLine(freehand[-1], self.world_to_screen(self._freehand_cursor))
            painter.setBrush(QBrush(QColor("#FFFFFF")))
            painter.setPen(QPen(QColor("#8B5CF6"), 1.5))
            step = max(1, len(freehand) // 40)
            for index, point in enumerate(freehand):
                if index % step:
                    continue
                painter.drawEllipse(point, 3.0, 3.0)

        if self._split_points:
            split_line = QPolygonF([self.world_to_screen(point) for point in self._split_points])
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#DC2626"), 2.0, Qt.PenStyle.DashLine))
            painter.drawPolyline(split_line)
            if self._split_cursor is not None:
                painter.drawLine(split_line[-1], self.world_to_screen(self._split_cursor))

        if (
            not self._regions
            and not self._draft_points
            and not self._freehand_points
            and self._world_width > 0
            and self._world_height > 0
        ):
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
        if self._split_mode:
            self._split_cursor = self.screen_to_world(point)
            if self._split_dragging and event.buttons() & Qt.MouseButton.LeftButton:
                self._add_split_screen_point(point)
            self._emit_coordinate(point)
            self.update()
            event.accept()
            return
        if self._freehand_mode:
            self._freehand_cursor = self.screen_to_world(point)
            if self._freehand_dragging and event.buttons() & Qt.MouseButton.LeftButton:
                self._add_freehand_screen_point(point)
            self._emit_coordinate(point)
            self.update()
            event.accept()
            return
        if self._draw_mode:
            self._draft_cursor = self.screen_to_world(point)
            self._emit_coordinate(point)
            self.update()
            event.accept()
            return
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
        if self._freehand_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                self._freehand_dragging = True
                self._freehand_points.clear()
                self._freehand_last_screen = None
                self._add_freehand_screen_point(event.position())
                self.setFocus()
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                self.cancel_freehand()
                event.accept()
                return
            super().mousePressEvent(event)
            return
        if self._split_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                self._split_dragging = True
                self._split_points.clear()
                self._split_last_screen = None
                self._add_split_screen_point(event.position())
                self.setFocus()
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                self.cancel_split()
                event.accept()
                return
            super().mousePressEvent(event)
            return
        if self._draw_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                if (
                    len(self._draft_points) >= 3
                    and self._draft_points
                    and (
                        self.world_to_screen(self._draft_points[0]) - event.position()
                    ).manhattanLength()
                    <= 12
                ):
                    self.finish_drawing()
                else:
                    self.add_draft_point(event.position())
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                self.remove_last_draft_point()
                event.accept()
                return
            super().mousePressEvent(event)
            return
        should_pan = (
            event.button() == Qt.MouseButton.MiddleButton
            or self._pan_mode
            or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
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
        if self._freehand_mode and event.button() == Qt.MouseButton.LeftButton:
            if self._freehand_dragging:
                self._add_freehand_screen_point(event.position())
                self.finish_freehand()
            event.accept()
            return
        if self._split_mode and event.button() == Qt.MouseButton.LeftButton:
            if self._split_dragging:
                self._add_split_screen_point(event.position())
                self.finish_split()
            event.accept()
            return
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
        if self._draw_mode:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_drawing()
                event.accept()
                return
        if self._freehand_mode:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_freehand()
                event.accept()
                return
        if self._split_mode:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_split()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                self.cancel_split()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                self.cancel_freehand()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                self.remove_last_draft_point()
                event.accept()
                return
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

    @staticmethod
    def _union_geometries(geometries):
        if not geometries:
            return None
        try:
            return unary_union(geometries)
        except (GEOSException, ValueError):
            return None

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
        world_rect = [
            self.screen_to_world(rect.topLeft()),
            self.screen_to_world(rect.bottomRight()),
        ]
        selection = box(
            min(point.x() for point in world_rect),
            min(point.y() for point in world_rect),
            max(point.x() for point in world_rect),
            max(point.y() for point in world_rect),
        )
        for region_id, record in self._regions.items():
            if not record["region"].visible:
                continue
            geometry = record["geometry"]
            if geometry is None:
                continue
            if contained:
                if geometry.within(selection):
                    result.append(region_id)
            elif geometry.intersects(selection):
                result.append(region_id)
        return result
