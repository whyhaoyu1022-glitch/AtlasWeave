"""Validated, immutable commands for creating top-level regions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from shapely.errors import GEOSException
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import split as split_geometry
from shapely.validation import explain_validity

from atlasweave.domain.geometry import Point
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region, RegionStatus
from atlasweave.domain.topology import Edge, Face, HalfEdge, Ring, Vertex


class RegionCommandError(ValueError):
    """A region command cannot produce a valid project snapshot."""


def polygon_from_freehand(points, *, tolerance: float) -> Polygon:
    """Turn a sampled stroke into a cleaned polygon before command validation."""
    coordinates = [(float(point.x()), float(point.y())) for point in points]
    if len(coordinates) < 3:
        raise RegionCommandError("自由手绘区域至少需要三个采样点。")
    deduped = [coordinates[0]]
    for coordinate in coordinates[1:]:
        if coordinate != deduped[-1]:
            deduped.append(coordinate)
    if len(deduped) < 3:
        raise RegionCommandError("自由手绘轨迹包含的有效点不足。")
    if deduped[0] != deduped[-1]:
        deduped.append(deduped[0])
    line = LineString(deduped)
    cleaned = line.simplify(max(float(tolerance), 1e-9), preserve_topology=True)
    cleaned_coordinates = list(cleaned.coords)
    if len(cleaned_coordinates) < 4:
        raise RegionCommandError("自由手绘轨迹简化后无法形成闭合区域。")
    if cleaned_coordinates[0] != cleaned_coordinates[-1]:
        cleaned_coordinates.append(cleaned_coordinates[0])
    polygon = Polygon(cleaned_coordinates)
    if len(polygon.exterior.coords) < 4:
        raise RegionCommandError("自由手绘轨迹无法形成多边形。")
    return polygon


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


def split_region_into_children(
    project: Project,
    region_id: str,
    cut_points,
    child_names: tuple[str, str],
    type_name: str,
) -> tuple[Project, tuple[Region, Region]]:
    """Split one simple face into two real child regions with a shared edge."""
    if region_id not in project.regions:
        raise RegionCommandError("请选择要切分的区域。")
    parent = project.regions[region_id]
    if parent.locked:
        raise RegionCommandError(f"区域“{parent.name}”已锁定，不能切分。")
    if len(project.hierarchy_levels) < 2:
        raise RegionCommandError("工程至少需要两个行政层级才能创建子区域。")
    if len(parent.face_ids) != 1:
        raise RegionCommandError("本批切分只支持没有飞地的单面区域。")
    names = tuple(name.strip() for name in child_names)
    if len(names) != 2 or any(not name for name in names):
        raise RegionCommandError("两个子区域名称都不能为空。")
    if names[0] == names[1]:
        raise RegionCommandError("两个子区域名称不能重复。")
    if not type_name.strip():
        raise RegionCommandError("子区域类型不能为空。")
    try:
        parent_polygon = project.topology.face_polygon(parent.face_ids[0])
    except (KeyError, ValueError, GEOSException) as exc:
        raise RegionCommandError(f"父区域几何无法验证：{exc}") from exc
    if not parent_polygon.is_valid or len(parent_polygon.interiors):
        raise RegionCommandError("本批切分要求父区域是无洞的有效单面多边形。")
    coordinates = [(float(point.x()), float(point.y())) for point in cut_points]
    if len(coordinates) < 2:
        raise RegionCommandError("切分线至少需要两个点。")
    cut_line = LineString(coordinates)
    if not cut_line.is_valid or cut_line.length <= project.tolerances.minimum_edge_length:
        raise RegionCommandError("切分线无效或长度过短。")
    if not cut_line.intersects(parent_polygon.boundary):
        raise RegionCommandError("切分线必须穿过父区域边界。")
    try:
        pieces = list(split_geometry(parent_polygon, cut_line).geoms)
    except (GEOSException, ValueError) as exc:
        raise RegionCommandError(f"切分计算失败：{exc}") from exc
    pieces = [piece for piece in pieces if isinstance(piece, Polygon) and piece.area > 0]
    if len(pieces) != 2:
        raise RegionCommandError("切分线必须把父区域分成两个有效子区域。")
    tolerance = project.tolerances
    if abs(sum(piece.area for piece in pieces) - parent_polygon.area) > max(
        tolerance.minimum_face_area, parent_polygon.area * 1e-8
    ):
        raise RegionCommandError("切分结果面积与父区域不守恒。")

    old_topology = project.topology
    vertices = dict(old_topology.vertices)
    edges = dict(old_topology.edges)
    half_edges = dict(old_topology.half_edges)
    rings = dict(old_topology.rings)
    faces = dict(old_topology.faces)
    vertex_by_coordinate = {(vertex.x, vertex.y): vertex.id for vertex in vertices.values()}
    edge_by_key: dict[tuple[tuple[float, float], tuple[float, float]], str] = {}
    for edge in edges.values():
        start = vertices[edge.start_vertex_id].point.as_tuple()
        end = vertices[edge.end_vertex_id].point.as_tuple()
        edge_by_key[tuple(sorted((start, end)))] = edge.id

    def vertex_id(coordinate: tuple[float, float]) -> str:
        coordinate = (round(coordinate[0], 12), round(coordinate[1], 12))
        if coordinate not in vertex_by_coordinate:
            vertex = Vertex(x=coordinate[0], y=coordinate[1])
            vertices[vertex.id] = vertex
            vertex_by_coordinate[coordinate] = vertex.id
        return vertex_by_coordinate[coordinate]

    child_face_ids: list[str] = []
    for piece in pieces:
        points = list(orient(piece, sign=1.0).exterior.coords)[:-1]
        half_ids: list[str] = []
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            start_key = (round(start[0], 12), round(start[1], 12))
            end_key = (round(end[0], 12), round(end[1], 12))
            edge_key = tuple(sorted((start_key, end_key)))
            start_vertex = vertex_id(start_key)
            end_vertex = vertex_id(end_key)
            edge_id = edge_by_key.get(edge_key)
            if edge_id is None:
                edge = Edge(start_vertex_id=start_vertex, end_vertex_id=end_vertex)
                edges[edge.id] = edge
                edge_by_key[edge_key] = edge.id
                reversed_edge = False
            else:
                edge = edges[edge_id]
                reversed_edge = (
                    edge.start_vertex_id == end_vertex and edge.end_vertex_id == start_vertex
                )
                if not reversed_edge and (
                    edge.start_vertex_id != start_vertex or edge.end_vertex_id != end_vertex
                ):
                    raise RegionCommandError("切分结果无法建立共享边拓扑。")
            half = HalfEdge(edge_id=edge.id, reversed=reversed_edge)
            half_edges[half.id] = half
            half_ids.append(half.id)
        ring = Ring(half_edge_ids=tuple(half_ids))
        rings[ring.id] = ring
        face = Face(outer_ring_id=ring.id)
        faces[face.id] = face
        child_face_ids.append(face.id)

    topology = replace(
        old_topology,
        vertices=vertices,
        edges=edges,
        half_edges=half_edges,
        rings=rings,
        faces=faces,
        geometry_version=old_topology.geometry_version + 1,
    )
    child_level = project.hierarchy_levels[1]
    children = tuple(
        Region(
            name=name,
            type_name=type_name.strip(),
            level_id=child_level.id,
            parent_id=parent.id,
            face_ids=(face_id,),
            status=RegionStatus.VALID,
        )
        for name, face_id in zip(names, child_face_ids, strict=True)
    )
    updated_parent = replace(parent, visible=False)
    return (
        replace(
            project,
            regions={
                **project.regions,
                parent.id: updated_parent,
                **{child.id: child for child in children},
            },
            topology=topology,
            modified_at=datetime.now(UTC),
        ),
        children,
    )
