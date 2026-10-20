import pytest
from shapely.geometry import Polygon

from atlasweave.commands.region_commands import RegionCommandError, create_top_level_region
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
