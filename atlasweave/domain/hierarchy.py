"""User-defined administrative levels (§4.2)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from atlasweave.domain.values import (
    boolean_value,
    integer_value,
    json_object,
    new_id,
    text_value,
    uuid_value,
)


@dataclass(frozen=True, kw_only=True)
class HierarchyLevel:
    order: int
    name: str
    default_type_name: str = "区域"
    id: str = field(default_factory=new_id)
    boundary_style_key: str = "default"
    label_style_key: str = "default"
    enabled: bool = True
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid_value(self.id, "id")
        integer_value(self.order, "order")
        text_value(self.name, "name", required=True)
        text_value(self.default_type_name, "default_type_name", required=True)
        text_value(self.boundary_style_key, "boundary_style_key", required=True)
        text_value(self.label_style_key, "label_style_key", required=True)
        boolean_value(self.enabled, "enabled")
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))
