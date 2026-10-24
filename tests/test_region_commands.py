import pytest
from PyQt6.QtCore import QPointF
from shapely.geometry import Polygon

from atlasweave.commands.region_commands import (
    RegionCommandError,
    create_top_level_region,
    polygon_from_freehand,
    preview_region_split,
    split_region_into_children,
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
