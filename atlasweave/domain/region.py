"""Region records reference faces rather than owning duplicate boundary geometry."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from atlasweave.domain.geometry import Point
from atlasweave.domain.values import (
    ModelError,
    boolean_value,
    integer_value,
    json_object,
    new_id,
    number_value,
    text_value,
    uuid_sequence,
    uuid_value,
)


class RegionStatus(StrEnum):
    DRAFT = "draft"
    VALID = "valid"
    INVALID = "invalid"


class LabelPositionMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


@dataclass(frozen=True, kw_only=True)
class Region:
    name: str
    level_id: str
    id: str = field(default_factory=new_id)
    parent_id: str | None = None
    short_name: str = ""
    code: str = ""
    type_name: str = "区域"
    capital_name: str = ""
    population: int | None = None
    gdp: float | None = None
    gdp_unit: str = "亿"
    area_override_km2: float | None = None
    notes: str = ""
    custom_values: Mapping = field(default_factory=dict)
    face_ids: tuple[str, ...] = ()
    fill_color: str | None = None
    label_position: Point | None = None
    label_position_mode: LabelPositionMode = LabelPositionMode.AUTO
    visible: bool = True
    locked: bool = False
    status: RegionStatus = RegionStatus.DRAFT
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid_value(self.id, "id")
        uuid_value(self.level_id, "level_id")
        if self.parent_id is not None:
            uuid_value(self.parent_id, "parent_id")
        for name in (
            "name",
            "short_name",
            "code",
            "type_name",
            "capital_name",
            "gdp_unit",
            "notes",
        ):
            text_value(getattr(self, name), name)
        if self.population is not None:
            integer_value(self.population, "population")
        for name in ("gdp", "area_override_km2"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, number_value(getattr(self, name), name, minimum=0))
        for name in ("visible", "locked"):
            boolean_value(getattr(self, name), name)
        for name, enum in (("status", RegionStatus), ("label_position_mode", LabelPositionMode)):
            try:
                object.__setattr__(self, name, enum(getattr(self, name)))
            except (ValueError, TypeError) as exc:
                raise ModelError(name, "无效的状态值") from exc
        if self.label_position is not None and not isinstance(self.label_position, Point):
            raise ModelError("label_position", "必须是世界坐标点")
        if self.label_position_mode == LabelPositionMode.MANUAL and self.label_position is None:
            raise ModelError("label_position", "手动标签必须指定位置")
        if self.fill_color is not None and (
            not isinstance(self.fill_color, str)
            or not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.fill_color)
        ):
            raise ModelError("fill_color", "颜色必须是 #RRGGBB")
        object.__setattr__(self, "face_ids", uuid_sequence(self.face_ids, "face_ids"))
        object.__setattr__(self, "custom_values", json_object(self.custom_values, "custom_values"))
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))
