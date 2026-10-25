import pytest
from PyQt6.QtCore import QPointF
from shapely.geometry import Polygon

from atlasweave.commands.region_commands import (
    RegionCommandError,
    add_region_component,
    create_top_level_region,
    delete_simple_vertex,
    insert_vertex_on_edge,
    merge_sibling_regions,
    move_shared_vertex,
    polygon_from_freehand,
    preview_region_split,
    region_adjacency,
    split_region_into_children,
    split_regions_in_batch,
)
from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.services.project_io import ProjectStore


def test_create_top_level_region_returns_valid_immutable_snapshot(tmp_path):
    project, region = create_top_level_region(
        Project(name="命令测试"), Polygon(((10, 10), (40, 10), (25, 40))), "国家", "国家"
    )

    assert region.id in project.regions
    assert len(project.topology.faces) == 1
    assert project.topology.face_polygon(region.face_ids[0]).is_valid
    path = tmp_path / "drawn.atlasweave"
    ProjectStore().save(project, path)
    loaded = ProjectStore().load(path)
    assert loaded.project.regions[region.id].name == "国家"
    assert loaded.project.topology.face_polygon(region.face_ids[0]).area == pytest.approx(450)


def test_create_top_level_region_rejects_overlap_and_out_of_bounds():
    project, _ = create_top_level_region(
        Project(name="重叠测试"), Polygon(((10, 10), (40, 10), (25, 40))), "已有", "国家"
    )
    with pytest.raises(RegionCommandError, match="重叠"):
        create_top_level_region(project, Polygon(((20, 15), (45, 15), (30, 35))), "重叠", "国家")
    with pytest.raises(RegionCommandError, match="世界边界"):
        create_top_level_region(
            project, Polygon(((1190, 90), (1210, 90), (1190, 110))), "越界", "国家"
        )


def test_polygon_from_freehand_closes_and_simplifies_stroke():
    polygon = polygon_from_freehand(
        [
            QPointF(10, 10),
            QPointF(10.01, 10.01),
            QPointF(40, 10),
            QPointF(40, 40),
            QPointF(10, 40),
        ],
        tolerance=0.05,
    )

    assert polygon.is_valid
    assert polygon.exterior.is_ring
    assert polygon.area == pytest.approx(900, abs=1)


def test_split_region_creates_children_with_shared_edge():
    project = Project(
        name="切分测试",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
            HierarchyLevel(order=2, name="市级", default_type_name="市"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((10, 10), (90, 10), (90, 90), (10, 90))), "国家", "国家"
    )

    split_project, children = split_region_into_children(
        project,
        parent.id,
        [QPointF(-10, 50), QPointF(110, 50)],
        ("北省", "南省"),
        "省",
    )

    assert len(children) == 2
    assert not split_project.regions[parent.id].visible
    assert all(child.parent_id == parent.id for child in children)
    assert sum(
        split_project.topology.face_polygon(child.face_ids[0]).area for child in children
    ) == pytest.approx(6400)
    edge_uses = split_project.topology.edge_uses(tuple(child.face_ids[0] for child in children))
    assert any(len(uses) == 2 for uses in edge_uses.values())


def test_split_preview_supports_multiple_pieces_and_next_level():
    project = Project(
        name="多段切分测试",
        world_width_km=120,
        world_height_km=120,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
            HierarchyLevel(order=2, name="市级", default_type_name="市"),
        ),
    )
    u_shape = Polygon(
        ((0, 0), (100, 0), (100, 100), (60, 100), (60, 40), (40, 40), (40, 100), (0, 100))
    )
    project, parent = create_top_level_region(project, u_shape, "国家", "国家")
    cut = [QPointF(-10, 30), QPointF(110, 70), QPointF(-10, 90)]

    preview = preview_region_split(project, parent.id, cut)
    assert len(preview.pieces) == 5
    assert preview.total_area == pytest.approx(u_shape.area)
    split_project, children = split_region_into_children(
        project, parent.id, cut, tuple(f"省{i}" for i in range(1, 6)), "省"
    )

    assert len(children) == 5
    assert all(child.level_id == project.hierarchy_levels[1].id for child in children)
    assert all(child.parent_id == parent.id for child in children)
    edge_uses = split_project.topology.edge_uses(tuple(child.face_ids[0] for child in children))
    assert sum(len(uses) == 2 for uses in edge_uses.values()) >= 4


def test_split_child_uses_next_level_and_rejects_existing_children():
    project = Project(
        name="层级切分测试",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
            HierarchyLevel(order=2, name="市级", default_type_name="市"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((10, 10), (90, 10), (90, 90), (10, 90))), "国家", "国家"
    )
    project, provinces = split_region_into_children(
        project,
        parent.id,
        [QPointF(-10, 50), QPointF(110, 50)],
        ("北省", "南省"),
        "省",
    )

    project, cities = split_region_into_children(
        project,
        provinces[0].id,
        [QPointF(0, 30), QPointF(100, 30)],
        ("北市", "南市"),
        "市",
    )
    assert all(city.level_id == project.hierarchy_levels[2].id for city in cities)
    with pytest.raises(RegionCommandError, match="不可见|已经包含子区域"):
        split_region_into_children(
            project,
            provinces[0].id,
            [QPointF(0, 20), QPointF(100, 20)],
            ("重复市一", "重复市二"),
            "市",
        )


def test_advanced_parent_resplit_hides_old_children_and_replaces_partition():
    project = Project(
        name="父级重切分",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((0, 0), (100, 0), (100, 100), (0, 100))), "国家", "国家"
    )
    project, old_children = split_region_into_children(
        project,
        parent.id,
        [QPointF(-10, 50), QPointF(110, 50)],
        ("旧北", "旧南"),
        "省",
    )
    after, new_children = split_region_into_children(
        project,
        parent.id,
        [QPointF(-10, 30), QPointF(110, 30), QPointF(-10, 70)],
        ("新一", "新二", "新三"),
        "省",
        allow_existing_children=True,
    )
    assert len(new_children) == 3
    assert all(not after.regions[child.id].visible for child in old_children)
    assert all(child.parent_id == parent.id for child in new_children)
    assert sum(
        after.topology.face_polygon(child.face_ids[0]).area for child in new_children
    ) == pytest.approx(10000)


def test_batch_split_applies_multiple_validated_steps_in_one_snapshot():
    project = Project(
        name="六区连续切分",
        world_width_km=120,
        world_height_km=120,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
            HierarchyLevel(order=2, name="市级", default_type_name="市"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((0, 0), (100, 0), (100, 60), (0, 60))), "国家", "国家"
    )
    first_project, first_children = split_region_into_children(
        project,
        parent.id,
        [QPointF(-10, 10), QPointF(110, 10)],
        ("一省", "余下"),
        "省",
    )
    remaining = first_children[1]
    batch_steps = ((remaining.id, [QPointF(-10, 20), QPointF(110, 20)], ("二省", "余下二")),)
    after_batch, created = split_regions_in_batch(first_project, batch_steps, "省")
    assert len(created) == 2
    assert sum(
        after_batch.topology.face_polygon(face_id).area
        for region in after_batch.regions.values()
        if region.visible
        for face_id in region.face_ids
    ) == pytest.approx(6000)
    assert all(region.parent_id in after_batch.regions for region in created)


def test_single_polyline_can_create_six_sibling_regions():
    project = Project(
        name="六区切分",
        world_width_km=120,
        world_height_km=120,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((0, 0), (100, 0), (100, 60), (0, 60))), "国家", "国家"
    )
    ys = (8, 16, 24, 32, 40, 48)
    cut = [QPointF(-10 if index % 2 == 0 else 110, y) for index, y in enumerate(ys)]
    after, children = split_region_into_children(
        project, parent.id, cut, tuple(f"省{i}" for i in range(1, 7)), "省"
    )
    assert len(children) == 6
    assert sum(
        after.topology.face_polygon(child.face_ids[0]).area for child in children
    ) == pytest.approx(6000)
    edge_uses = after.topology.edge_uses(tuple(child.face_ids[0] for child in children))
    assert sum(len(uses) == 2 for uses in edge_uses.values()) >= 5


def test_split_multi_component_parent_preserves_all_components():
    project = Project(
        name="多部件重切分",
        world_width_km=120,
        world_height_km=120,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
            HierarchyLevel(order=2, name="市级", default_type_name="市"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((5, 5), (35, 5), (35, 35), (5, 35))), "国家", "国家"
    )
    project, parent = add_region_component(
        project, parent.id, Polygon(((65, 65), (95, 65), (95, 95), (65, 95)))
    )
    preview = preview_region_split(project, parent.id, [QPointF(-10, 20), QPointF(110, 20)])
    assert len(preview.pieces) == 3
    after, children = split_region_into_children(
        project, parent.id, [QPointF(-10, 20), QPointF(110, 20)], ("甲", "乙", "飞地"), "省"
    )
    assert len(children) == 3
    assert sum(
        after.topology.face_polygon(face_id).area
        for child in children
        for face_id in child.face_ids
    ) == pytest.approx(1800)


def test_merge_adjacent_siblings_preserves_first_uuid_and_adjacency():
    project = Project(
        name="合并测试",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((0, 0), (100, 0), (100, 100), (0, 100))), "国家", "国家"
    )
    project, children = split_region_into_children(
        project,
        parent.id,
        [QPointF(-10, 50), QPointF(110, 50)],
        ("北省", "南省"),
        "省",
    )
    assert region_adjacency(project, children[0].id) == (children[1].id,)

    merged_project, merged = merge_sibling_regions(
        project, [children[0].id, children[1].id], "合并省"
    )

    assert merged.id == children[0].id
    assert merged.name == "合并省"
    assert not merged_project.regions[children[1].id].visible
    assert merged_project.topology.face_polygon(merged.face_ids[0]).area == pytest.approx(10000)
    assert region_adjacency(merged_project, merged.id) == ()


def test_merge_multiple_adjacent_siblings_into_one_region():
    project = Project(
        name="批量合并测试",
        world_width_km=120,
        world_height_km=120,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, parent = create_top_level_region(
        project,
        Polygon(((0, 0), (100, 0), (100, 100), (60, 100), (60, 40), (40, 40), (40, 100), (0, 100))),
        "国家",
        "国家",
    )
    project, children = split_region_into_children(
        project,
        parent.id,
        [QPointF(-10, 30), QPointF(110, 70), QPointF(-10, 90)],
        tuple(f"省{i}" for i in range(1, 6)),
        "省",
    )
    merged_project, merged = merge_sibling_regions(project, [child.id for child in children])
    assert merged.id == children[0].id
    assert merged_project.topology.face_polygon(merged.face_ids[0]).area == pytest.approx(8800)
    assert all(not merged_project.regions[child.id].visible for child in children[1:])


def test_merge_non_adjacent_siblings_creates_multi_component_region():
    project = Project(
        name="非相邻合并",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, first = create_top_level_region(
        project, Polygon(((5, 5), (25, 5), (25, 25), (5, 25))), "甲", "省"
    )
    project, second = create_top_level_region(
        project, Polygon(((65, 65), (85, 65), (85, 85), (65, 85))), "乙", "省"
    )
    merged_project, merged = merge_sibling_regions(project, [first.id, second.id], "甲乙")
    assert merged.id == first.id
    assert merged.name == "甲乙"
    assert len(merged.face_ids) == 2
    assert not merged_project.regions[second.id].visible
    assert sum(
        merged_project.topology.face_polygon(face_id).area for face_id in merged.face_ids
    ) == pytest.approx(800)


def test_merge_ring_of_regions_preserves_hole_ring():
    project = Project(name="洞合并", world_width_km=100, world_height_km=100)
    pieces = (
        Polygon(((20, 60), (80, 60), (80, 100), (20, 100))),
        Polygon(((20, 0), (80, 0), (80, 40), (20, 40))),
        Polygon(((0, 40), (40, 40), (40, 60), (0, 60))),
        Polygon(((60, 40), (100, 40), (100, 60), (60, 60))),
    )
    regions = []
    for index, piece in enumerate(pieces):
        project, region = create_top_level_region(project, piece, f"环{index}", "区域")
        regions.append(region)
    merged_project, merged = merge_sibling_regions(
        project, [region.id for region in regions], "带洞区域"
    )
    merged_shape = merged_project.topology.face_polygon(merged.face_ids[0])
    assert len(merged_shape.interiors) == 1
    assert merged_shape.area == pytest.approx(6400)


def test_add_region_component_preserves_one_region_node_and_faces():
    project, region = create_top_level_region(
        Project(name="飞地测试", world_width_km=100, world_height_km=100),
        Polygon(((10, 10), (40, 10), (40, 40), (10, 40))),
        "国家",
        "国家",
    )
    updated_project, updated = add_region_component(
        project, region.id, Polygon(((60, 60), (70, 60), (70, 70), (60, 70)))
    )
    assert updated.id == region.id
    assert len(updated.face_ids) == 2
    assert sum(
        updated_project.topology.face_polygon(face_id).area for face_id in updated.face_ids
    ) == pytest.approx(1000)


def test_move_shared_vertex_updates_both_faces_and_rejects_invalid_geometry():
    project = Project(
        name="共享顶点测试",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((0, 0), (100, 0), (100, 100), (0, 100))), "国家", "国家"
    )
    project, children = split_region_into_children(
        project, parent.id, [QPointF(-10, 50), QPointF(110, 50)], ("北省", "南省"), "省"
    )
    shared_vertex = next(
        vertex.id
        for vertex in project.topology.vertices.values()
        if abs(vertex.x) < 1e-9 and abs(vertex.y - 50) < 1e-9
    )
    from atlasweave.domain.geometry import Point

    moved = move_shared_vertex(project, shared_vertex, Point(0, 45))
    assert moved.topology.vertices[shared_vertex].y == 45
    assert sum(
        moved.topology.face_polygon(child.face_ids[0]).area for child in children
    ) == pytest.approx(10000)
    with pytest.raises(RegionCommandError):
        move_shared_vertex(project, "00000000-0000-4000-8000-000000000000", Point(0, 0))


def test_insert_vertex_on_shared_edge_updates_both_rings():
    project = Project(
        name="插点测试",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((0, 0), (100, 0), (100, 100), (0, 100))), "国家", "国家"
    )
    project, children = split_region_into_children(
        project, parent.id, [QPointF(-10, 50), QPointF(110, 50)], ("北省", "南省"), "省"
    )
    edge_uses = project.topology.edge_uses(tuple(child.face_ids[0] for child in children))
    shared_edge_id = next(edge_id for edge_id, uses in edge_uses.items() if len(uses) == 2)
    edge = project.topology.edges[shared_edge_id]
    start = project.topology.vertices[edge.start_vertex_id]
    end = project.topology.vertices[edge.end_vertex_id]
    from atlasweave.domain.geometry import Point

    updated = insert_vertex_on_edge(
        project, shared_edge_id, Point((start.x + end.x) / 2, (start.y + end.y) / 2)
    )
    assert shared_edge_id not in updated.topology.edges
    assert sum(
        updated.topology.face_polygon(child.face_ids[0]).area for child in children
    ) == pytest.approx(10000)


def test_delete_inserted_shared_vertex_restores_edge_count_and_area():
    project = Project(
        name="删点测试",
        world_width_km=100,
        world_height_km=100,
        hierarchy_levels=(
            HierarchyLevel(order=0, name="国家级", default_type_name="国家"),
            HierarchyLevel(order=1, name="省级", default_type_name="省"),
        ),
    )
    project, parent = create_top_level_region(
        project, Polygon(((0, 0), (100, 0), (100, 100), (0, 100))), "国家", "国家"
    )
    project, children = split_region_into_children(
        project, parent.id, [QPointF(-10, 50), QPointF(110, 50)], ("北省", "南省"), "省"
    )
    edge_uses = project.topology.edge_uses(tuple(child.face_ids[0] for child in children))
    shared_edge_id = next(edge_id for edge_id, uses in edge_uses.items() if len(uses) == 2)
    edge = project.topology.edges[shared_edge_id]
    start = project.topology.vertices[edge.start_vertex_id]
    end = project.topology.vertices[edge.end_vertex_id]
    from atlasweave.domain.geometry import Point

    inserted = insert_vertex_on_edge(
        project, shared_edge_id, Point((start.x + end.x) / 2, (start.y + end.y) / 2)
    )
    new_vertex = next(
        vertex.id
        for vertex in inserted.topology.vertices.values()
        if abs(vertex.x - (start.x + end.x) / 2) < 1e-9
        and abs(vertex.y - (start.y + end.y) / 2) < 1e-9
    )
    deleted = delete_simple_vertex(inserted, new_vertex)
    assert new_vertex not in deleted.topology.vertices
    assert sum(
        deleted.topology.face_polygon(child.face_ids[0]).area for child in children
    ) == pytest.approx(10000)
