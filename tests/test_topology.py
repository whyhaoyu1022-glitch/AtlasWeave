import random
from dataclasses import replace

import pytest

from atlasweave.domain.geometry import Point, Tolerances
from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region
from atlasweave.domain.topology import Edge, Face, HalfEdge, Ring, Topology, validate_topology
from atlasweave.domain.validation import validate_project
from atlasweave.domain.values import ModelError, new_id
from tests.domain_helpers import MapFixture, neighboring_squares

TOLERANCE = Tolerances.for_world(1200, 800)


def codes(topology):
    return {issue.code for issue in validate_topology(topology, TOLERANCE).issues}


def project_for_faces(topology, *face_groups):
    level = HierarchyLevel(order=0, name="国家级")
    regions = [
        Region(name=f"区域{i}", level_id=level.id, face_ids=faces, status="valid")
        for i, faces in enumerate(face_groups)
    ]
    return Project(hierarchy_levels=(level,), topology=topology, regions={r.id: r for r in regions})


def test_neighboring_faces_share_exactly_one_edge_and_opposite_directions():
    _, topology, left, right, shared = neighboring_squares()
    assert len(topology.vertices) == 6 and len(topology.edges) == 7
    uses = topology.edge_uses((left, right))[shared]
    assert len(uses) == 2 and uses[0][2] != uses[1][2]
    assert topology.adjacency((left, right)) == {left: frozenset({right}), right: frozenset({left})}
    assert validate_topology(topology, TOLERANCE).is_valid
    assert validate_project(project_for_faces(topology, (left,), (right,))).is_valid


@pytest.mark.parametrize("seed", range(16))
def test_shared_boundary_candidate_moves_both_sides_without_gap_or_area_loss(seed):
    _, before, left, right, shared = neighboring_squares()
    offset = random.Random(seed).uniform(-0.2, 0.2)
    sample = Point(1 + offset, 0.5)
    after = before.with_edge_samples(shared, (sample,))
    a, b = after.face_polygon(left), after.face_polygon(right)
    assert sample in after.ring_points(after.faces[left].outer_ring_id)
    assert sample in after.ring_points(after.faces[right].outer_ring_id)
    assert a.intersection(b).area == pytest.approx(0, abs=1e-12)
    assert a.union(b).area == pytest.approx(2)
    assert a.boundary.intersection(b.boundary).length > 1
    assert before.edges[shared].samples == ()
    assert after.edges[shared].id == before.edges[shared].id
    assert after.geometry_version == before.geometry_version + 1
    assert validate_project(project_for_faces(after, (left,), (right,))).is_valid


def test_vertex_move_updates_all_incident_edges_and_preserves_original_snapshot():
    fixture, before, left, right, _ = neighboring_squares()
    vertex_id = fixture.vertex_at[(1, 1)]
    after = before.with_vertex_position(vertex_id, Point(1.2, 1))
    assert after.face_polygon(left).area == pytest.approx(1.1)
    assert after.face_polygon(right).area == pytest.approx(0.9)
    assert before.vertices[vertex_id].point == Point(1, 1)
    assert after.vertices[vertex_id].id == vertex_id
    assert validate_project(project_for_faces(after, (left,), (right,))).is_valid


def test_three_region_junction_keeps_all_incident_boundaries_connected():
    fixture = MapFixture()
    left = fixture.face([(0, 0), (1, 0), (1, 0.5), (1, 1), (0, 1)])
    bottom = fixture.face([(1, 0), (2, 0), (2, 0.5), (1, 0.5)])
    top = fixture.face([(1, 0.5), (2, 0.5), (2, 1), (1, 1)])
    before = fixture.topology()
    after = before.with_vertex_position(fixture.vertex_at[(1, 0.5)], Point(1.1, 0.55))
    project = project_for_faces(after, (left,), (bottom,), (top,))
    assert validate_project(project).is_valid
    assert sum(after.face_polygon(face).area for face in (left, bottom, top)) == pytest.approx(2)
    assert after.adjacency((left, bottom, top))[left] == frozenset({bottom, top})


def test_point_contacts_are_not_edge_adjacency():
    fixture, _, left, _, _ = neighboring_squares()
    island = fixture.face([(2, 1), (3, 1), (3, 2), (2, 2)])
    topology = fixture.topology()
    assert topology.adjacency((left, island))[left] == frozenset()


def test_islands_and_holes_are_preserved_and_validated():
    fixture = MapFixture()
    mainland = fixture.face(
        [(0, 0), (4, 0), (4, 4), (0, 4)], holes=([(1, 1), (1, 2), (2, 2), (2, 1)],)
    )
    island = fixture.face([(5, 0), (6, 0), (6, 1), (5, 1)])
    topology = fixture.topology()
    assert topology.face_polygon(mainland).area == 15
    project = project_for_faces(topology, (mainland, island))
    report = validate_project(project)
    assert report.is_valid
    assert "multipart_region" in {issue.code for issue in report.issues}


def test_parent_surface_can_share_outer_edges_with_children_in_same_direction():
    fixture = MapFixture()
    parent_face = fixture.face([(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (0, 1)])
    left = fixture.face([(0, 0), (1, 0), (1, 1), (0, 1)])
    right = fixture.face([(1, 0), (2, 0), (2, 1), (1, 1)])
    top = HierarchyLevel(order=0, name="国家级")
    sub = HierarchyLevel(order=1, name="省级")
    parent = Region(name="国家", level_id=top.id, face_ids=(parent_face,), status="valid")
    children = [
        Region(name=name, level_id=sub.id, parent_id=parent.id, face_ids=(face,), status="valid")
        for name, face in (("左省", left), ("右省", right))
    ]
    project = Project(
        hierarchy_levels=(top, sub),
        topology=fixture.topology(),
        regions={r.id: r for r in [parent, *children]},
    )
    assert validate_project(project).is_valid


def test_dangling_references_are_reported_at_each_graph_layer():
    edge = Edge(start_vertex_id=new_id(), end_vertex_id=new_id())
    half = HalfEdge(edge_id=new_id())
    ring = Ring(half_edge_ids=(new_id(),))
    face = Face(outer_ring_id=new_id())
    topology = Topology(
        edges={edge.id: edge},
        half_edges={half.id: half},
        rings={ring.id: ring},
        faces={face.id: face},
    )
    assert {
        "missing_vertex",
        "missing_edge",
        "missing_ring_reference",
        "missing_face_ring",
    } <= codes(topology)


@pytest.mark.parametrize("collection", ["vertices", "edges", "half_edges", "rings", "faces"])
def test_mismatched_mapping_id_returns_diagnostic_instead_of_crashing(collection):
    _, topology, _, _, _ = neighboring_squares()
    objects = dict(getattr(topology, collection))
    key, value = next(iter(objects.items()))
    objects[key] = replace(value, id=new_id())
    changed = replace(topology, **{collection: objects})
    assert "mapping_id_mismatch" in codes(changed)


def test_disconnected_and_unclosed_rings_are_not_silently_closed():
    _, topology, left, _, _ = neighboring_squares()
    ring_id = topology.faces[left].outer_ring_id
    ring = topology.rings[ring_id]
    for half_ids in (ring.half_edge_ids[:-1], tuple(reversed(ring.half_edge_ids))):
        rings = dict(topology.rings)
        rings[ring_id] = replace(ring, half_edge_ids=half_ids)
        assert "invalid_ring" in codes(replace(topology, rings=rings))


def test_self_intersections_small_faces_and_hole_orientation_are_rejected():
    fixture = MapFixture()
    fixture.face([(0, 0), (2, 2), (0, 2), (2, 0)])
    assert {"self_intersection", "invalid_face", "small_face"} <= codes(fixture.topology())
    fixture = MapFixture()
    fixture.face([(0, 0), (4, 0), (4, 4), (0, 4)], holes=([(1, 1), (2, 1), (2, 2), (1, 2)],))
    assert "inner_ring_direction" in codes(fixture.topology())
    fixture = MapFixture()
    fixture.face([(0, 0), (4, 0), (4, 4), (0, 4)], holes=([(5, 5), (5, 6), (6, 6), (6, 5)],))
    assert "invalid_face" in codes(fixture.topology())


def test_duplicate_boundaries_and_near_zero_segments_are_rejected():
    _, topology, _, _, shared = neighboring_squares()
    clone = replace(topology.edges[shared], id=new_id())
    assert "duplicate_edge_geometry" in codes(
        replace(topology, edges={**topology.edges, clone.id: clone})
    )
    duplicate_start = topology.vertices[topology.edges[shared].start_vertex_id].point
    assert "short_edge" in codes(topology.with_edge_samples(shared, (duplicate_start,)))


def test_reserved_curve_is_reported_not_replaced_with_a_straight_line():
    _, topology, left, _, shared = neighboring_squares()
    edges = dict(topology.edges)
    edges[shared] = replace(edges[shared], curve={"future_curve": [1, 2]})
    changed = replace(topology, edges=edges)
    assert "unsupported_curve" in codes(changed)
    with pytest.raises(ModelError):
        changed.face_polygon(left)


def test_candidate_with_out_of_world_geometry_is_invalid():
    fixture, topology, left, right, _ = neighboring_squares()
    changed = topology.with_vertex_position(fixture.vertex_at[(0, 0)], Point(-1, 0))
    assert "outside_world" in {
        i.code for i in validate_project(project_for_faces(changed, (left,), (right,))).errors
    }
