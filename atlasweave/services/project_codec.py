"""Explicit JSON codecs for immutable domain records, including unknown fields."""

from collections.abc import Mapping
from dataclasses import MISSING, fields
from datetime import datetime
from enum import Enum

from atlasweave.domain.geometry import Point
from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region
from atlasweave.domain.topology import Edge, Face, HalfEdge, Ring, Topology, Vertex
from atlasweave.domain.values import ModelError


class UnsupportedDataError(ValueError):
    """An internal structure cannot be represented without losing unknown data."""


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _point(value: Point):
    return [round(value.x, 8), round(value.y, 8)]


def _decode_point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise UnsupportedDataError("坐标含无法安全保留的附加结构")
    return Point(*value)


def _record(model) -> dict:
    known = {item.name for item in fields(model) if item.name != "extra_fields"}
    if known & set(model.extra_fields):
        raise ModelError("extra_fields", "扩展字段与已有字段冲突，无法安全保存")
    result = thaw(model.extra_fields)
    if not isinstance(result, dict):
        raise UnsupportedDataError(f"{model.__class__.__name__} 扩展字段必须是对象")
    for name in known:
        value = getattr(model, name)
        if isinstance(value, Point):
            result[name] = _point(value)
        elif name == "samples":
            result[name] = [_point(point) for point in value]
        elif isinstance(model, Vertex) and name in ("x", "y"):
            result[name] = round(value, 8)
        else:
            result[name] = thaw(value)
    return result


def _object(raw, name):
    if not isinstance(raw, Mapping):
        raise ModelError(name, "必须是 JSON 对象")
    return raw


def _decode_record(model, raw):
    raw = _object(raw, model.__name__)
    declared = {item.name: item for item in fields(model) if item.name != "extra_fields"}
    required = {
        name
        for name, item in declared.items()
        if item.default is MISSING and item.default_factory is MISSING
    }
    required.add("id")
    if required - raw.keys():
        raise ModelError(model.__name__, f"缺少字段：{', '.join(sorted(required - raw.keys()))}")
    known = {key: value for key, value in raw.items() if key in declared}
    known["extra_fields"] = {key: value for key, value in raw.items() if key not in declared}
    if model is Region and known.get("label_position") is not None:
        known["label_position"] = _decode_point(known["label_position"])
    if model is Edge and "samples" in known:
        known["samples"] = tuple(_decode_point(point) for point in known["samples"])
    return model(**known)


PROJECT_KEYS = frozenset(
    {
        "format_version",
        "app_version",
        "project",
        "hierarchy_levels",
        "regions",
        "custom_field_definitions",
        "metadata",
        "extensions",
    }
)
PROJECT_DETAIL_KEYS = frozenset(
    {
        "id",
        "name",
        "description",
        "world_width_km",
        "world_height_km",
        "created_at",
        "modified_at",
    }
)


def encode_project(project: Project, document_extra: Mapping) -> dict[str, dict]:
    if set(document_extra) & PROJECT_KEYS or set(project.extra_fields) & PROJECT_DETAIL_KEYS:
        raise ModelError("extra_fields", "工程扩展字段与已知字段冲突")
    detail = thaw(project.extra_fields)
    if not isinstance(detail, dict):
        raise UnsupportedDataError("工程扩展字段必须是对象")
    detail.update({name: thaw(getattr(project, name)) for name in PROJECT_DETAIL_KEYS})
    for name in ("world_width_km", "world_height_km"):
        detail[name] = round(detail[name], 8)
    root = thaw(document_extra)
    if not isinstance(root, dict):
        raise UnsupportedDataError("工程根扩展字段必须是对象")
    root.update(
        {
            "format_version": project.format_version,
            "app_version": project.app_version,
            "project": detail,
            "hierarchy_levels": [_record(level) for level in project.hierarchy_levels],
            "regions": {key: _record(region) for key, region in project.regions.items()},
            "custom_field_definitions": thaw(project.custom_field_definitions),
            "metadata": thaw(project.metadata),
            "extensions": thaw(project.extensions),
        }
    )
    topology = project.topology
    names = set(topology.collections()) | {"geometry_version"}
    if set(topology.extra_fields) & names:
        raise ModelError("topology.extra_fields", "拓扑扩展字段与已知字段冲突")
    graph = thaw(topology.extra_fields)
    graph.update(
        {
            name: {key: _record(record) for key, record in records.items()}
            for name, records in topology.collections().items()
        }
    )
    graph["geometry_version"] = topology.geometry_version
    return {"project.json": root, "topology.json": graph, "styles.json": thaw(project.map_style)}


def decode_project(documents: Mapping[str, Mapping]) -> tuple[Project, dict]:
    root = _object(documents["project.json"], "project.json")
    detail = _object(root["project"], "project")
    if PROJECT_DETAIL_KEYS - detail.keys():
        raise ModelError("project", "缺少工程基本信息字段")
    known = {key: value for key, value in detail.items() if key in PROJECT_DETAIL_KEYS}
    for name in ("created_at", "modified_at"):
        if not isinstance(known[name], str):
            raise ModelError(name, "必须是 ISO 8601 时间文本")
        known[name] = datetime.fromisoformat(known[name])
    raw_graph = _object(documents["topology.json"], "topology.json")
    graph = {}
    for name, model in (
        ("vertices", Vertex),
        ("edges", Edge),
        ("half_edges", HalfEdge),
        ("rings", Ring),
        ("faces", Face),
    ):
        graph[name] = {
            key: _decode_record(model, value)
            for key, value in _object(raw_graph[name], name).items()
        }
    graph["geometry_version"] = raw_graph.get("geometry_version", 0)
    graph["extra_fields"] = {key: value for key, value in raw_graph.items() if key not in graph}
    known.update(
        {
            "format_version": root["format_version"],
            "app_version": root["app_version"],
            "hierarchy_levels": tuple(
                _decode_record(HierarchyLevel, row) for row in root["hierarchy_levels"]
            ),
            "regions": {
                key: _decode_record(Region, row)
                for key, row in _object(root["regions"], "regions").items()
            },
            "topology": Topology(**graph),
            "map_style": _object(documents["styles.json"], "styles.json"),
            "custom_field_definitions": root.get("custom_field_definitions", ()),
            "metadata": root.get("metadata", {}),
            "extensions": root.get("extensions", {}),
            "extra_fields": {
                key: value for key, value in detail.items() if key not in PROJECT_DETAIL_KEYS
            },
        }
    )
    return Project(**known), {key: value for key, value in root.items() if key not in PROJECT_KEYS}
