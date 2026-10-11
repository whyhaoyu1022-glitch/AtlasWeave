from dataclasses import replace

from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region
from atlasweave.domain.validation import validate_project
from atlasweave.domain.values import new_id
from tests.domain_helpers import MapFixture, neighboring_squares
from tests.test_topology import project_for_faces


def codes(project):
    return {issue.code for issue in validate_project(project).issues}


def test_empty_draft_allowed_empty_valid_and_missing_faces_rejected():
    project = Project()
    region = Region(name="草稿", level_id=project.hierarchy_levels[0].id)
    assert validate_project(replace(project, regions={region.id: region})).is_valid
    valid = replace(region, status="valid")
    assert "empty_valid_region" in codes(replace(project, regions={valid.id: valid}))
    missing = replace(region, face_ids=(new_id(),))
    assert "missing_face" in codes(replace(project, regions={missing.id: missing}))


def test_overlapping_siblings_are_rejected():
    fixture = MapFixture()
    left = fixture.face([(0, 0), (2, 0), (2, 2), (0, 2)])
    right = fixture.face([(1, 1), (3, 1), (3, 3), (1, 3)])
    assert "sibling_overlap" in codes(project_for_faces(fixture.topology(), (left,), (right,)))


def test_child_outside_parent_is_rejected():
    fixture = MapFixture()
    parent_face = fixture.face([(0, 0), (2, 0), (2, 2), (0, 2)])
    child_face = fixture.face([(1, 1), (3, 1), (3, 3), (1, 3)])
    parent_level = HierarchyLevel(order=0, name="国家级")
    child_level = HierarchyLevel(order=1, name="省级")
    parent = Region(name="国家", level_id=parent_level.id, face_ids=(parent_face,), status="valid")
    child = Region(
        name="省",
        level_id=child_level.id,
        parent_id=parent.id,
        face_ids=(child_face,),
        status="valid",
    )
    project = Project(
        hierarchy_levels=(parent_level, child_level),
        topology=fixture.topology(),
        regions={r.id: r for r in (parent, child)},
    )
    assert "child_outside_parent" in codes(project)
    draft_parent = replace(parent, face_ids=(), status="draft")
    assert "parent_geometry_missing" in codes(
        replace(project, regions={parent.id: draft_parent, child.id: child})
    )


def test_same_face_cannot_be_reused_as_two_sibling_regions():
    _, topology, left, _, _ = neighboring_squares()
    assert {"sibling_overlap", "same_shared_direction"} <= codes(
        project_for_faces(topology, (left,), (left,))
    )


def test_three_region_edge_ownership_is_rejected():
    _, topology, left, _, _ = neighboring_squares()
    assert "non_manifold_edge" in codes(project_for_faces(topology, (left,), (left,), (left,)))


def test_duplicate_coordinate_graphs_do_not_pass_as_shared_topology():
    fixture = MapFixture()
    left = fixture.face([(0, 0), (1, 0), (1, 1), (0, 1)])
    # Deliberately independent IDs at the same coordinates.
    fixture.vertex_at.clear()
    fixture.edge_between.clear()
    right = fixture.face([(1, 0), (2, 0), (2, 1), (1, 1)])
    assert {"duplicate_vertex_coordinate", "duplicate_edge_geometry", "unshared_boundary"} <= codes(
        project_for_faces(fixture.topology(), (left,), (right,))
    )


def test_invalid_marker_cannot_be_hidden_by_valid_geometry():
    _, topology, left, _, _ = neighboring_squares()
    project = project_for_faces(topology, (left,))
    region = next(iter(project.regions.values()))
    invalid = replace(region, status="invalid", visible=False, locked=True)
    assert "marked_invalid" in codes(replace(project, regions={invalid.id: invalid}))
