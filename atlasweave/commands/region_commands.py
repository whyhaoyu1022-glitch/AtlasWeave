"""Validated, immutable commands for creating top-level regions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from shapely.errors import GEOSException
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

from atlasweave.domain.geometry import Point
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region, RegionStatus
from atlasweave.domain.topology import Edge, Face, HalfEdge, Ring, Vertex


class RegionCommandError(ValueError):
    """A region command cannot produce a valid project snapshot."""


def create_top_level_region(
    project: Project,
    polygon: Polygon,
    name: str,
    type_name: str,
) -> tuple[Project, Region]:
    """Return a new immutable project snapshot containing one valid top-level face."""
    if not name.strip():
        raise RegionCommandError("区域名称不能为空。")
    if not type_name.strip():
        raise RegionCommandError("区域类型不能为空。")
    if not polygon.is_valid:
        raise RegionCommandError(f"区域几何无效：{explain_validity(polygon)}")
    tolerance = project.tolerances
    if polygon.area <= tolerance.minimum_face_area:
        raise RegionCommandError("区域面积为零或小于工程容差。")
    min_x, min_y, max_x, max_y = polygon.bounds
    if min_x < 0 or min_y < 0 or max_x > project.world_width_km or max_y > project.world_height_km:
        raise RegionCommandError("区域不能超出世界边界。")
    for existing_region in project.regions.values():
        if not existing_region.visible:
            continue
        for face_id in existing_region.face_ids:
            try:
                existing = project.topology.face_polygon(face_id)
            except (KeyError, ValueError, GEOSException) as exc:
                raise RegionCommandError(
                    f"现有区域“{existing_region.name}”的几何无法验证：{exc}"
                ) from exc
            if existing.intersection(polygon).area > tolerance.minimum_face_area:
                raise RegionCommandError(f"区域与已有区域“{existing_region.name}”重叠。")

    oriented = orient(polygon, sign=1.0)
    points = [Point(x, y) for x, y in list(oriented.exterior.coords)[:-1]]
    vertices = [Vertex(x=point.x, y=point.y) for point in points]
    edges = [
        Edge(
            start_vertex_id=vertices[index].id,
            end_vertex_id=vertices[(index + 1) % len(vertices)].id,
        )
        for index in range(len(vertices))
    ]
    half_edges = [HalfEdge(edge_id=edge.id) for edge in edges]
    ring = Ring(half_edge_ids=tuple(half.id for half in half_edges))
    face = Face(outer_ring_id=ring.id)
    old_topology = project.topology
    topology = replace(
        old_topology,
        vertices={**old_topology.vertices, **{item.id: item for item in vertices}},
        edges={**old_topology.edges, **{item.id: item for item in edges}},
        half_edges={**old_topology.half_edges, **{item.id: item for item in half_edges}},
        rings={**old_topology.rings, ring.id: ring},
        faces={**old_topology.faces, face.id: face},
        geometry_version=old_topology.geometry_version + 1,
    )
    region = Region(
        name=name.strip(),
        type_name=type_name.strip(),
        level_id=project.hierarchy_levels[0].id,
        face_ids=(face.id,),
        status=RegionStatus.VALID,
    )
    return (
        replace(
            project,
            regions={**project.regions, region.id: region},
            topology=topology,
            modified_at=datetime.now(UTC),
        ),
        region,
    )
