import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid1

import pytest

from atlasweave.domain.geometry import Point
from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.domain.region import LabelPositionMode, Region, RegionStatus
from atlasweave.domain.validation import validate_project
from atlasweave.domain.values import ModelError, new_id


def test_defaults_use_stable_v4_ids_and_world_coordinates():
    project = Project()
    assert project.name == "未命名世界"
    assert project.world_width_km == 1200.0 and project.world_height_km == 800.0
    assert UUID(project.id).version == 4
    assert UUID(project.hierarchy_levels[0].id).version == 4
    assert replace(project, name="苍海联邦").id == project.id
    assert project.created_at.tzinfo is not None
    assert validate_project(project).is_valid
    assert project.tolerances.minimum_face_area == pytest.approx(9.6e-5)


@pytest.mark.parametrize(
    "change",
    [
        {"name": " "},
        {"name": "世" * 81},
        {"description": "x" * 5001},
        {"world_width_km": 0},
        {"world_height_km": 1_000_001},
        {"world_width_km": float("inf")},
        {"world_height_km": float("nan")},
        {"world_width_km": True},
        {"id": str(uuid1())},
        {"id": "missing"},
        {"created_at": datetime(2026, 1, 1)},
    ],
)
def test_project_rejects_invalid_input(change):
    with pytest.raises(ModelError):
        Project(**change)


def test_project_timestamp_order_and_timezone_conversion():
    created = datetime(2026, 1, 1, tzinfo=UTC)
    assert Project(created_at=created, modified_at=created).created_at == created
    with pytest.raises(ModelError, match="modified_at"):
        Project(created_at=created, modified_at=created - timedelta(seconds=1))


@pytest.mark.parametrize(
    "change",
    [
        {"population": -1},
        {"population": 2.5},
        {"population": True},
        {"gdp": -1},
        {"gdp": float("nan")},
        {"gdp": float("inf")},
        {"area_override_km2": -1},
        {"locked": 1},
        {"visible": "true"},
        {"status": "fake"},
        {"fill_color": "red"},
        {"fill_color": "#12345678"},
        {"label_position_mode": "manual"},
        {"label_position": (10, 20)},
    ],
)
def test_region_rejects_invalid_input(change):
    with pytest.raises(ModelError):
        Region(name="北省", level_id=new_id(), **change)


def test_region_fields_support_drafts_manual_labels_and_zero_population():
    region = Region(
        name="北省",
        level_id=new_id(),
        population=0,
        gdp=0,
        fill_color="#D9E8FB",
        label_position=Point(20, 30),
        label_position_mode="manual",
        locked=True,
    )
    assert region.status == RegionStatus.DRAFT
    assert region.label_position_mode == LabelPositionMode.MANUAL
    assert region.gdp == 0.0 and region.gdp_unit == "亿"
    assert {"face_ids", "custom_values", "area_override_km2", "capital_name", "notes"} <= {
        item.name for item in fields(region)
    }


def test_snapshots_isolate_input_and_preserve_unknown_extension_values():
    future = {
        "terrain_layers": [{"future": {"palette": ["#112233", 1, None]}}],
        "new_module": {"enabled": True},
    }
    project = Project(extensions=future, extra_fields={"unrecognized": "保留"})
    future["terrain_layers"][0]["future"]["palette"].append("changed")
    assert project.extensions["terrain_layers"][0]["future"]["palette"] == ("#112233", 1, None)
    assert project.extensions["new_module"]["enabled"] is True
    assert project.extra_fields["unrecognized"] == "保留"
    with pytest.raises(TypeError):
        project.extensions["new_module"]["enabled"] = False
    with pytest.raises(FrozenInstanceError):
        project.name = "changed"


def test_region_maps_cannot_be_changed_by_caller():
    level = HierarchyLevel(order=0, name="国家级")
    region = Region(name="北国", level_id=level.id)
    source = {region.id: region}
    project = Project(hierarchy_levels=(level,), regions=source)
    source.clear()
    assert project.regions[region.id] is region
    with pytest.raises(TypeError):
        project.regions[region.id] = replace(region, name="changed")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "10"])
def test_points_reject_invalid_coordinates(value):
    with pytest.raises(ModelError):
        Point(value, 0)


def test_domain_imports_without_qt_or_screen():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys,json; "
            "from atlasweave.domain.project import Project; p=Project(); "
            "print(json.dumps([n for n in sys.modules if n.startswith('PyQt6')]))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == []
