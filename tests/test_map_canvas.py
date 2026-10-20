from math import isclose

from PyQt6.QtCore import QPoint, QPointF, Qt

from atlasweave.domain.project import Project
from atlasweave.domain.region import Region
from atlasweave.domain.topology import Edge, Face, HalfEdge, Ring, Topology, Vertex
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


def _square_region(*, visible=True):
    project = Project()
    level_id = project.hierarchy_levels[0].id
    vertices = [Vertex(x=x, y=y) for x, y in ((10, 10), (40, 10), (40, 40), (10, 40))]
    edges = [
        Edge(start_vertex_id=vertices[index].id, end_vertex_id=vertices[(index + 1) % 4].id)
        for index in range(4)
    ]
    half_edges = [HalfEdge(edge_id=edge.id) for edge in edges]
    ring = Ring(half_edge_ids=tuple(half.id for half in half_edges))
    face = Face(outer_ring_id=ring.id)
    topology = Topology(
        vertices={vertex.id: vertex for vertex in vertices},
        edges={edge.id: edge for edge in edges},
        half_edges={half.id: half for half in half_edges},
        rings={ring.id: ring},
        faces={face.id: face},
    )
    region = Region(name="测试区域", level_id=level_id, face_ids=(face.id,), visible=visible)
    return region, topology


def test_real_regions_are_hit_tested_and_selection_is_signal_driven(qtbot):
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.set_world(100, 100)
    region, topology = _square_region()
    canvas.set_regions({region.id: region}, topology)
    signals = []
    canvas.selection_changed.connect(signals.append)

    center = canvas.world_to_screen(QPointF(25, 25))
    assert canvas._hit_test(center) == region.id
    canvas.set_selection([region.id])
    assert canvas.selected_region_ids == {region.id}
    assert canvas.selected_area_km2 == 900
    assert signals[-1] == (region.id,)


def test_hidden_regions_are_not_hit_but_can_be_selected_from_tree(qtbot):
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.set_world(100, 100)
    region, topology = _square_region(visible=False)
    canvas.set_regions({region.id: region}, topology)

    assert canvas._hit_test(canvas.world_to_screen(QPointF(25, 25))) is None
    canvas.set_selection([region.id])
    assert canvas.selected_region_ids == {region.id}


def test_mouse_click_and_containment_box_use_real_world_geometry(qtbot):
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.set_world(100, 100)
    region, topology = _square_region()
    canvas.set_regions({region.id: region}, topology)

    center = canvas.world_to_screen(QPointF(25, 25))
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=center.toPoint())
    assert canvas.selected_region_ids == {region.id}
    selection = canvas.world_rect_on_screen()
    assert region.id in canvas._regions_in_rect(selection, contained=True)
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    assert not canvas.selected_region_ids


def test_drawing_mode_collects_points_and_supports_cancel(qtbot):
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.set_world(100, 100)
    completed = []
    canvas.polygon_completed.connect(completed.append)
    canvas.set_draw_mode(True)
    points = [QPointF(10, 10), QPointF(40, 10), QPointF(25, 40)]
    for point in points:
        canvas.add_draft_point(canvas.world_to_screen(point))

    assert len(canvas.draft_points) == 3
    assert canvas.finish_drawing()
    assert len(completed) == 1
    assert len(completed[0]) == 3
    canvas.remove_last_draft_point()
    assert len(canvas.draft_points) == 2
    canvas.cancel_drawing()
    assert not canvas.draft_points
