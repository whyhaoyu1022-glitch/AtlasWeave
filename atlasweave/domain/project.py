"""Immutable project snapshots; mutations will be assembled by commands."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from atlasweave import __version__
from atlasweave.domain.geometry import Tolerances
from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.region import Region
from atlasweave.domain.topology import Topology
from atlasweave.domain.values import (
    ModelError,
    json_object,
    new_id,
    number_value,
    object_map,
    text_value,
    timestamp_value,
    uuid_value,
)


def default_levels() -> tuple[HierarchyLevel, ...]:
    return (HierarchyLevel(order=0, name="国家级", default_type_name="国家"),)


def reserved_extensions() -> dict:
    return dict.fromkeys(
        (
            "terrain_layers",
            "hydrology_layers",
            "settlements",
            "transport_layers",
            "timeline",
            "custom_feature_layers",
        ),
        (),
    )


def default_map_style() -> dict:
    return {
        "background_color": "#FFFFFF",
        "outside_color": "#DDE2E7",
        "show_labels": True,
        "show_legend": False,
        "show_title": False,
    }


@dataclass(frozen=True, kw_only=True)
class Project:
    name: str = "未命名世界"
    id: str = field(default_factory=new_id)
    format_version: str = "1.0.0"
    app_version: str = __version__
    description: str = ""
    world_width_km: float = 1200.0
    world_height_km: float = 800.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    modified_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    hierarchy_levels: tuple[HierarchyLevel, ...] = field(default_factory=default_levels)
    regions: Mapping[str, Region] = field(default_factory=dict)
    topology: Topology = field(default_factory=Topology)
    map_style: Mapping = field(default_factory=default_map_style)
    custom_field_definitions: tuple[Mapping, ...] = ()
    metadata: Mapping = field(default_factory=dict)
    extensions: Mapping = field(default_factory=reserved_extensions)
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid_value(self.id, "id")
        text_value(self.name, "name", required=True, maximum=80)
        text_value(self.description, "description", maximum=5000)
        text_value(self.format_version, "format_version", required=True)
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.format_version):
            raise ModelError("format_version", "必须使用 major.minor.patch 格式")
        text_value(self.app_version, "app_version", required=True)
        for name in ("world_width_km", "world_height_km"):
            object.__setattr__(
                self,
                name,
                number_value(getattr(self, name), name, minimum=1, maximum=1_000_000),
            )
        timestamp_value(self.created_at, "created_at")
        timestamp_value(self.modified_at, "modified_at")
        if self.modified_at < self.created_at:
            raise ModelError("modified_at", "不得早于创建时间")
        if not isinstance(self.hierarchy_levels, (tuple, list)) or any(
            not isinstance(level, HierarchyLevel) for level in self.hierarchy_levels
        ):
            raise ModelError("hierarchy_levels", "必须是行政层级序列")
        object.__setattr__(self, "hierarchy_levels", tuple(self.hierarchy_levels))
        object.__setattr__(self, "regions", object_map(self.regions, Region, "regions"))
        if not isinstance(self.topology, Topology):
            raise ModelError("topology", "必须是 Topology")
        for name in ("map_style", "metadata", "extensions", "extra_fields"):
            object.__setattr__(self, name, json_object(getattr(self, name), name))
        if not isinstance(self.custom_field_definitions, (tuple, list)):
            raise ModelError("custom_field_definitions", "必须是字段定义序列")
        object.__setattr__(
            self,
            "custom_field_definitions",
            tuple(
                json_object(item, f"custom_field_definitions[{i}]")
                for i, item in enumerate(self.custom_field_definitions)
            ),
        )

    @property
    def tolerances(self) -> Tolerances:
        return Tolerances.for_world(self.world_width_km, self.world_height_km)
