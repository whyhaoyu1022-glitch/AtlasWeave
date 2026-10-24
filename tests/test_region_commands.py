import pytest
from PyQt6.QtCore import QPointF
from shapely.geometry import Polygon

from atlasweave.commands.region_commands import (
    RegionCommandError,
    create_top_level_region,
    polygon_from_freehand,
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
