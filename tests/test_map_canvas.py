from math import isclose

from PyQt6.QtCore import QPoint, QPointF, Qt

from atlasweave.ui.map_canvas import MapCanvas


def test_world_transform_round_trips_and_respects_view_margin(qtbot):
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.set_world(1000, 500)

    world = QPointF(275, 125)
    screen = canvas.world_to_screen(world)
    round_trip = canvas.screen_to_world(screen)

    assert isclose(round_trip.x(), world.x(), abs_tol=1e-6)
    assert isclose(round_trip.y(), world.y(), abs_tol=1e-6)
    rect = canvas.world_rect_on_screen()
    assert rect.left() >= canvas.VIEW_MARGIN - 1
    assert rect.top() >= canvas.VIEW_MARGIN - 1
    assert rect.right() <= canvas.width() - canvas.VIEW_MARGIN + 1
    assert rect.bottom() <= canvas.height() - canvas.VIEW_MARGIN + 1


def test_zoom_keeps_cursor_world_position_and_clamps_limits(qtbot):
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.set_world(1200, 800)
    anchor = QPointF(230, 210)
    before = canvas.screen_to_world(anchor)

    canvas.set_zoom_percent(350, anchor)
    after = canvas.screen_to_world(anchor)

    assert isclose(after.x(), before.x(), abs_tol=1e-6)
    assert isclose(after.y(), before.y(), abs_tol=1e-6)
    canvas.set_zoom_percent(1)
    assert canvas.zoom_percent == canvas.MIN_ZOOM_PERCENT
    canvas.set_zoom_percent(9000)
    assert canvas.zoom_percent == canvas.MAX_ZOOM_PERCENT


def test_pan_mode_moves_canvas_and_emits_inside_coordinate(qtbot):
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.set_world(1200, 800)
    coordinates = []
    canvas.coordinate_changed.connect(lambda x, y, inside: coordinates.append((x, y, inside)))
    canvas.set_pan_mode(True)
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(400, 300))
    qtbot.mouseMove(canvas, QPoint(460, 340))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(460, 340))

    assert canvas.pan_offset.x() != 0 or canvas.pan_offset.y() != 0
    assert coordinates and coordinates[-1][2]
