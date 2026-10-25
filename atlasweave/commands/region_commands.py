"""Validated, immutable commands for creating and splitting regions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import hypot

from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.polygon import orient
from shapely.ops import split as split_geometry
from shapely.ops import unary_union
from shapely.validation import explain_validity

from atlasweave.domain.geometry import Point
from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region, RegionStatus
from atlasweave.domain.topology import Edge, Face, HalfEdge, Ring, Vertex


class RegionCommandError(ValueError):
    """A region command cannot produce a valid project snapshot."""


@dataclass(frozen=True)
class SplitPreview:
    """Immutable geometry-only result shown before a split is committed."""

    region_id: str
    child_level: HierarchyLevel
    pieces: tuple[Polygon, ...]

    @property
    def minimum_area(self) -> float:
        return min(piece.area for piece in self.pieces)

    @property
    def total_area(self) -> float:
        return sum(piece.area for piece in self.pieces)


def _topology_with_polygons(topology, geometries: Sequence[Polygon]):
    """Append polygon faces while reusing vertices and shared edges."""
    vertices = dict(topology.vertices)
    edges = dict(topology.edges)
    half_edges = dict(topology.half_edges)
    rings = dict(topology.rings)
    faces = dict(topology.faces)
    vertex_by_coordinate = {
        (round(vertex.x, 12), round(vertex.y, 12)): vertex.id for vertex in vertices.values()
    }
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

    def add_ring(coordinates) -> str:
        points = list(coordinates)[:-1]
        if len(points) < 3:
            raise RegionCommandError("结果环至少需要三个顶点。")
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
            else:
                edge = edges[edge_id]
                if (edge.start_vertex_id, edge.end_vertex_id) == (start_vertex, end_vertex):
                    pass
                elif (edge.start_vertex_id, edge.end_vertex_id) == (end_vertex, start_vertex):
                    pass
                else:
                    raise RegionCommandError("结果无法建立共享边拓扑。")
            reversed_edge = (
                edge.start_vertex_id == end_vertex and edge.end_vertex_id == start_vertex
            )
            half = HalfEdge(edge_id=edge.id, reversed=reversed_edge)
            half_edges[half.id] = half
            half_ids.append(half.id)
        ring = Ring(half_edge_ids=tuple(half_ids))
        rings[ring.id] = ring
        return ring.id

    face_ids: list[str] = []
    for geometry in geometries:
        if not isinstance(geometry, Polygon) or not geometry.is_valid:
            raise RegionCommandError("结果包含无效面。")
        oriented = orient(geometry, sign=1.0)
        outer_ring_id = add_ring(oriented.exterior.coords)
        inner_ring_ids = tuple(add_ring(ring.coords) for ring in oriented.interiors)
        face = Face(outer_ring_id=outer_ring_id, inner_ring_ids=inner_ring_ids)
        faces[face.id] = face
        face_ids.append(face.id)
    return (
        replace(
            topology,
            vertices=vertices,
            edges=edges,
            half_edges=half_edges,
            rings=rings,
            faces=faces,
            geometry_version=topology.geometry_version + 1,
        ),
        tuple(face_ids),
    )


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


def _point_coordinate(point) -> tuple[float, float]:
    """Read a Qt point or a plain two-item coordinate without coupling the domain to Qt."""
    if hasattr(point, "x") and callable(point.x):
        return float(point.x()), float(point.y())
    try:
        x, y = point
    except (TypeError, ValueError) as exc:
        raise RegionCommandError("切分线包含无法识别的坐标点。") from exc
    return float(x), float(y)


def _split_target(
    project: Project, region_id: str, *, allow_existing_children: bool = False
) -> tuple[Region, Polygon | MultiPolygon, HierarchyLevel]:
    if region_id not in project.regions:
        raise RegionCommandError("请选择要切分的区域。")
    parent = project.regions[region_id]
    if parent.locked:
        raise RegionCommandError(f"区域“{parent.name}”已锁定，不能切分。")
    if not parent.visible and not allow_existing_children:
        raise RegionCommandError(f"区域“{parent.name}”当前不可见，不能重复切分。")
    if (
        any(region.parent_id == parent.id for region in project.regions.values())
        and not allow_existing_children
    ):
        raise RegionCommandError("该区域已经包含子区域；请先完成子区域处理后再切分。")
    levels_by_id = {level.id: level for level in project.hierarchy_levels}
    parent_level = levels_by_id.get(parent.level_id)
    if parent_level is None:
        raise RegionCommandError("区域引用的行政层级不存在。")
    child_levels = sorted(
        (
            level
            for level in project.hierarchy_levels
            if level.enabled and level.order > parent_level.order
        ),
        key=lambda level: level.order,
    )
    if not child_levels:
        raise RegionCommandError("没有比当前区域更低的可用行政层级。")
    try:
        parent_polygon = unary_union(
            [project.topology.face_polygon(face_id) for face_id in parent.face_ids]
        )
    except (KeyError, ValueError, GEOSException) as exc:
        raise RegionCommandError(f"父区域几何无法验证：{exc}") from exc
    if not parent_polygon.is_valid or not isinstance(parent_polygon, (Polygon, MultiPolygon)):
        raise RegionCommandError("父区域必须是有效的多部件面。")
    parts = (
        tuple(parent_polygon.geoms)
        if isinstance(parent_polygon, MultiPolygon)
        else (parent_polygon,)
    )
    if any(len(part.interiors) for part in parts):
        raise RegionCommandError("父区域暂不支持带洞面切分。")
    return parent, parent_polygon, child_levels[0]


def preview_region_split(
    project: Project,
    region_id: str,
    cut_points,
    *,
    allow_existing_children: bool = False,
) -> SplitPreview:
    """Compute valid split pieces without changing the project snapshot."""
    parent, parent_polygon, child_level = _split_target(
        project, region_id, allow_existing_children=allow_existing_children
    )
    coordinates = [_point_coordinate(point) for point in cut_points]
    deduped: list[tuple[float, float]] = []
    for coordinate in coordinates:
        if not deduped or coordinate != deduped[-1]:
            deduped.append(coordinate)
    if len(deduped) < 2:
        raise RegionCommandError("切分线至少需要两个点。")
    cut_line = LineString(deduped)
    if not cut_line.is_valid or not cut_line.is_simple:
        raise RegionCommandError("切分线无效或存在自相交。")
    if cut_line.length <= project.tolerances.minimum_edge_length:
        raise RegionCommandError("切分线无效或长度过短。")
    if not cut_line.intersects(parent_polygon.boundary):
        raise RegionCommandError("切分线必须穿过父区域边界。")
    try:
        pieces = tuple(
            piece
            for piece in split_geometry(parent_polygon, cut_line).geoms
            if isinstance(piece, Polygon) and piece.area > 0
        )
    except (GEOSException, ValueError) as exc:
        raise RegionCommandError(f"切分计算失败：{exc}") from exc
    if len(pieces) < 2:
        raise RegionCommandError("切分线必须把父区域分成至少两个有效子区域。")
    tolerance = project.tolerances
    for piece in pieces:
        if not piece.is_valid or len(piece.interiors):
            raise RegionCommandError("切分结果包含无效面或洞。")
        if piece.area <= tolerance.minimum_face_area:
            raise RegionCommandError("切分结果包含小于工程容差的区域。")
    if abs(sum(piece.area for piece in pieces) - parent_polygon.area) > max(
        tolerance.minimum_face_area, parent_polygon.area * 1e-8
    ):
        raise RegionCommandError("切分结果面积与父区域不守恒。")
    return SplitPreview(region_id=parent.id, child_level=child_level, pieces=pieces)


def split_regions_in_batch(
    project: Project,
    steps: Sequence[tuple[str, Sequence, Sequence[str]]],
    type_name: str,
) -> tuple[Project, tuple[Region, ...]]:
    """Apply several real child splits as one validated, undoable batch.

    Each step is ``(parent_region_id, cut_points, child_names)``.  The helper
    deliberately reuses :func:`split_region_into_children` for every step, so
    each intermediate snapshot receives the same geometry, hierarchy and
    naming validation as an interactive split.  Callers can wrap the returned
    before/after snapshots in one ``ProjectSnapshotCommand``.
    """
    if not steps:
        raise RegionCommandError("连续切分至少需要一个步骤。")
    current = project
    created: list[Region] = []
    for step in steps:
        if len(step) != 3:
            raise RegionCommandError("连续切分步骤必须包含父区域、切分线和子区域名称。")
        region_id, cut_points, child_names = step
        current, children = split_region_into_children(
            current, region_id, cut_points, child_names, type_name
        )
        created.extend(children)
    return current, tuple(created)


def split_region_into_children(
    project: Project,
    region_id: str,
    cut_points,
    child_names: Sequence[str],
    type_name: str,
    *,
    allow_existing_children: bool = False,
) -> tuple[Project, tuple[Region, ...]]:
    """Split one simple face into two or more real child regions with shared edges."""
    parent = project.regions.get(region_id)
    if parent is None:
        raise RegionCommandError("请选择要切分的区域。")
    names = tuple(name.strip() for name in child_names)
    if any(not name for name in names):
        raise RegionCommandError("所有子区域名称都不能为空。")
    if len(set(names)) != len(names):
        raise RegionCommandError("子区域名称不能重复。")
    if not type_name.strip():
        raise RegionCommandError("子区域类型不能为空。")
    preview = preview_region_split(
        project, region_id, cut_points, allow_existing_children=allow_existing_children
    )
    pieces = preview.pieces
    if len(names) != len(pieces):
        raise RegionCommandError(f"请为 {len(pieces)} 个切分结果分别提供名称。")

    old_topology = project.topology
    vertices = dict(old_topology.vertices)
    edges = dict(old_topology.edges)
    half_edges = dict(old_topology.half_edges)
    rings = dict(old_topology.rings)
    faces = dict(old_topology.faces)
    vertex_by_coordinate = {
        (round(vertex.x, 12), round(vertex.y, 12)): vertex.id for vertex in vertices.values()
    }
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
    children = tuple(
        Region(
            name=name,
            type_name=type_name.strip(),
            level_id=preview.child_level.id,
            parent_id=parent.id,
            face_ids=(face_id,),
            status=RegionStatus.VALID,
        )
        for name, face_id in zip(names, child_face_ids, strict=True)
    )
    updated_parent = replace(parent, visible=False)
    old_children = {
        region.id: replace(region, visible=False)
        for region in project.regions.values()
        if allow_existing_children and region.parent_id == parent.id
    }
    return (
        replace(
            project,
            regions={
                **project.regions,
                parent.id: updated_parent,
                **old_children,
                **{child.id: child for child in children},
            },
            topology=topology,
            modified_at=datetime.now(UTC),
        ),
        children,
    )


def merge_sibling_regions(
    project: Project,
    region_ids: Sequence[str],
    merged_name: str | None = None,
    merged_type: str | None = None,
    population_policy: str = "sum",
    gdp_policy: str = "sum",
) -> tuple[Project, Region]:
    """Merge two or more adjacent simple siblings while retaining the first UUID."""
    ids = tuple(dict.fromkeys(region_ids))
    if len(ids) < 2 or any(region_id not in project.regions for region_id in ids):
        raise RegionCommandError("请选择至少两个相邻的同级区域。")
    regions = tuple(project.regions[region_id] for region_id in ids)
    first = regions[0]
    if any(
        region.level_id != first.level_id or region.parent_id != first.parent_id
        for region in regions[1:]
    ):
        raise RegionCommandError("只能合并同一父级下的同级区域。")
    if any(not region.visible for region in regions):
        raise RegionCommandError("不可见区域不能参与合并。")
    if any(region.locked for region in regions):
        raise RegionCommandError("锁定区域不能合并。")
    if any(
        child.parent_id in ids for child in project.regions.values() if child.parent_id is not None
    ):
        raise RegionCommandError("包含子区域的区域不能直接合并。")
    try:
        polygons = tuple(
            project.topology.face_polygon(face_id)
            for region in regions
            for face_id in region.face_ids
        )
    except (KeyError, ValueError, GEOSException) as exc:
        raise RegionCommandError(f"区域几何无法验证：{exc}") from exc
    merged = unary_union(polygons)
    if not isinstance(merged, (Polygon, MultiPolygon)) or not merged.is_valid:
        raise RegionCommandError("合并结果不是有效区域几何。")
    merged_parts = tuple(merged.geoms) if isinstance(merged, MultiPolygon) else (merged,)
    if any(part.area <= project.tolerances.minimum_face_area for part in merged_parts):
        raise RegionCommandError("合并结果包含小于工程容差的部件。")
    topology, merged_face_ids = _topology_with_polygons(project.topology, merged_parts)
    if population_policy not in {"sum", "primary", "empty"}:
        raise RegionCommandError("人口合并策略无效。")
    if gdp_policy not in {"sum", "primary", "empty"}:
        raise RegionCommandError("GDP 合并策略无效。")

    def combine(values, policy):
        if policy == "empty":
            return None
        if policy == "primary":
            return values[0]
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    merged_population = combine(tuple(region.population for region in regions), population_policy)
    merged_gdp = combine(tuple(region.gdp for region in regions), gdp_policy)
    updated_first = replace(
        first,
        name=(merged_name or first.name).strip() or first.name,
        type_name=(merged_type or first.type_name).strip() or first.type_name,
        population=merged_population,
        gdp=merged_gdp,
        face_ids=merged_face_ids,
    )
    updated_regions = {**project.regions, first.id: updated_first}
    updated_regions.update({region.id: replace(region, visible=False) for region in regions[1:]})
    return (
        replace(
            project,
            regions=updated_regions,
            topology=topology,
            modified_at=datetime.now(UTC),
        ),
        updated_first,
    )


def region_adjacency(project: Project, region_id: str) -> tuple[str, ...]:
    """Return visible same-level sibling IDs connected by shared topology edges."""
    region = project.regions.get(region_id)
    if region is None or not region.face_ids:
        return ()
    siblings = [
        candidate
        for candidate in project.regions.values()
        if candidate.visible
        and candidate.id != region.id
        and candidate.level_id == region.level_id
        and candidate.parent_id == region.parent_id
        and candidate.face_ids
    ]
    face_ids = tuple(region.face_ids) + tuple(
        face_id for item in siblings for face_id in item.face_ids
    )
    adjacent_faces = project.topology.adjacency(face_ids)
    target_neighbors = set().union(
        *(adjacent_faces.get(face_id, ()) for face_id in region.face_ids)
    )
    return tuple(
        sorted(item.id for item in siblings if target_neighbors.intersection(item.face_ids))
    )


def add_region_component(
    project: Project, region_id: str, polygon: Polygon
) -> tuple[Project, Region]:
    """Add a real island/enclave face to an existing visible region."""
    region = project.regions.get(region_id)
    if region is None:
        raise RegionCommandError("请选择要添加部件的区域。")
    if region.locked:
        raise RegionCommandError(f"区域“{region.name}”已锁定，不能添加部件。")
    if not polygon.is_valid or len(polygon.interiors):
        raise RegionCommandError("部件必须是无洞的有效多边形。")
    if polygon.area <= project.tolerances.minimum_face_area:
        raise RegionCommandError("部件面积为零或小于工程容差。")
    min_x, min_y, max_x, max_y = polygon.bounds
    if min_x < 0 or min_y < 0 or max_x > project.world_width_km or max_y > project.world_height_km:
        raise RegionCommandError("部件不能超出世界边界。")
    if region.parent_id is not None:
        parent = project.regions.get(region.parent_id)
        if parent is None or not parent.face_ids:
            raise RegionCommandError("区域父级几何不存在。")
        parent_polygon = unary_union(
            project.topology.face_polygon(face_id) for face_id in parent.face_ids
        )
        if not parent_polygon.covers(polygon):
            raise RegionCommandError("部件必须完全位于父区域内。")
    for other in project.regions.values():
        if not other.visible or other.id == region.id:
            continue
        for face_id in other.face_ids:
            if (
                project.topology.face_polygon(face_id).intersection(polygon).area
                > project.tolerances.minimum_face_area
            ):
                raise RegionCommandError(f"部件与已有区域“{other.name}”重叠。")

    vertices = dict(project.topology.vertices)
    edges = dict(project.topology.edges)
    half_edges = dict(project.topology.half_edges)
    rings = dict(project.topology.rings)
    faces = dict(project.topology.faces)
    vertex_by_coordinate = {
        (round(vertex.x, 12), round(vertex.y, 12)): vertex.id for vertex in vertices.values()
    }
    edge_by_key = {}
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

    points = list(orient(polygon, sign=1.0).exterior.coords)[:-1]
    half_ids = []
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
        else:
            edge = edges[edge_id]
        reversed_edge = edge.start_vertex_id == end_vertex and edge.end_vertex_id == start_vertex
        if not reversed_edge and (
            edge.start_vertex_id != start_vertex or edge.end_vertex_id != end_vertex
        ):
            raise RegionCommandError("部件无法建立共享边拓扑。")
        half = HalfEdge(edge_id=edge.id, reversed=reversed_edge)
        half_edges[half.id] = half
        half_ids.append(half.id)
    ring = Ring(half_edge_ids=tuple(half_ids))
    face = Face(outer_ring_id=ring.id)
    rings[ring.id] = ring
    faces[face.id] = face
    topology = replace(
        project.topology,
        vertices=vertices,
        edges=edges,
        half_edges=half_edges,
        rings=rings,
        faces=faces,
        geometry_version=project.topology.geometry_version + 1,
    )
    updated = replace(region, face_ids=(*region.face_ids, face.id))
    return (
        replace(
            project,
            regions={**project.regions, region.id: updated},
            topology=topology,
            modified_at=datetime.now(UTC),
        ),
        updated,
    )


def move_shared_vertex(project: Project, vertex_id: str, point: Point) -> Project:
    """Move one shared topology vertex and validate every affected region."""
    if vertex_id not in project.topology.vertices:
        raise RegionCommandError("要移动的顶点不存在。")
    candidate_topology = project.topology.with_vertex_position(vertex_id, point)
    affected_faces = []
    for face_id, face in candidate_topology.faces.items():
        ring = candidate_topology.rings.get(face.outer_ring_id)
        if ring is None:
            continue
        for half_id in ring.half_edge_ids:
            edge = candidate_topology.edges[candidate_topology.half_edges[half_id].edge_id]
            if vertex_id in {edge.start_vertex_id, edge.end_vertex_id}:
                affected_faces.append(face_id)
                break
    tolerance = project.tolerances
    for face_id in affected_faces:
        polygon = candidate_topology.face_polygon(face_id)
        if not polygon.is_valid or polygon.area <= tolerance.minimum_face_area:
            raise RegionCommandError("移动顶点会造成自交或无效面积。")
    candidate = replace(project, topology=candidate_topology, modified_at=datetime.now(UTC))
    for region in candidate.regions.values():
        if not region.visible:
            continue
        polygons = [candidate.topology.face_polygon(face_id) for face_id in region.face_ids]
        if region.parent_id is not None and region.parent_id in candidate.regions:
            parent = candidate.regions[region.parent_id]
            parent_shape = unary_union(
                [candidate.topology.face_polygon(face_id) for face_id in parent.face_ids]
            )
            if not all(parent_shape.covers(shape) for shape in polygons):
                raise RegionCommandError("移动顶点会使子区域越出父区域。")
        for other in candidate.regions.values():
            if (
                other.id != region.id
                and other.visible
                and other.level_id == region.level_id
                and other.parent_id == region.parent_id
            ):
                for shape in polygons:
                    for other_face in other.face_ids:
                        if (
                            shape.intersection(candidate.topology.face_polygon(other_face)).area
                            > tolerance.minimum_face_area
                        ):
                            raise RegionCommandError("移动顶点会造成同级区域重叠。")
    return candidate


def insert_vertex_on_edge(project: Project, edge_id: str, point: Point) -> Project:
    """Insert one shared vertex into every ring that references an edge."""
    edge = project.topology.edges.get(edge_id)
    if edge is None:
        raise RegionCommandError("要编辑的边不存在。")
    start = project.topology.vertices[edge.start_vertex_id].point
    end = project.topology.vertices[edge.end_vertex_id].point
    segment = LineString((start.as_tuple(), end.as_tuple()))
    if segment.distance(ShapelyPoint(point.as_tuple())) > project.tolerances.minimum_edge_length:
        raise RegionCommandError("新顶点必须位于目标边上。")
    if (
        hypot(point.x - start.x, point.y - start.y) <= project.tolerances.minimum_edge_length
        or hypot(point.x - end.x, point.y - end.y) <= project.tolerances.minimum_edge_length
    ):
        raise RegionCommandError("新顶点不能与边端点重合。")

    vertices = dict(project.topology.vertices)
    edges = dict(project.topology.edges)
    half_edges = dict(project.topology.half_edges)
    rings = dict(project.topology.rings)
    new_vertex = Vertex(x=point.x, y=point.y)
    vertices[new_vertex.id] = new_vertex
    first_edge = Edge(start_vertex_id=edge.start_vertex_id, end_vertex_id=new_vertex.id)
    second_edge = Edge(start_vertex_id=new_vertex.id, end_vertex_id=edge.end_vertex_id)
    edges[first_edge.id] = first_edge
    edges[second_edge.id] = second_edge
    touched_rings = 0
    old_half_ids = []
    for ring_id, ring in project.topology.rings.items():
        new_half_ids = []
        changed = False
        for half_id in ring.half_edge_ids:
            half = project.topology.half_edges[half_id]
            if half.edge_id != edge_id:
                new_half_ids.append(half_id)
                continue
            changed = True
            touched_rings += 1
            old_half_ids.append(half_id)
            if half.reversed:
                left = HalfEdge(edge_id=second_edge.id, reversed=True)
                right = HalfEdge(edge_id=first_edge.id, reversed=True)
            else:
                left = HalfEdge(edge_id=first_edge.id)
                right = HalfEdge(edge_id=second_edge.id)
            half_edges[left.id] = left
            half_edges[right.id] = right
            new_half_ids.extend((left.id, right.id))
        if changed:
            rings[ring_id] = replace(ring, half_edge_ids=tuple(new_half_ids))
    if touched_rings == 0:
        raise RegionCommandError("目标边没有被任何面引用。")
    edges.pop(edge_id, None)
    for half_id in old_half_ids:
        half_edges.pop(half_id, None)
    topology = replace(
        project.topology,
        vertices=vertices,
        edges=edges,
        half_edges=half_edges,
        rings=rings,
        geometry_version=project.topology.geometry_version + 1,
    )
    candidate = replace(project, topology=topology, modified_at=datetime.now(UTC))
    for region in candidate.regions.values():
        if not region.visible:
            continue
        for face_id in region.face_ids:
            polygon = candidate.topology.face_polygon(face_id)
            if not polygon.is_valid or polygon.area <= project.tolerances.minimum_face_area:
                raise RegionCommandError("插入顶点会造成无效几何。")
    return candidate


def delete_simple_vertex(project: Project, vertex_id: str) -> Project:
    """Delete a degree-two non-junction vertex and merge its two edges."""
    if vertex_id not in project.topology.vertices:
        raise RegionCommandError("要删除的顶点不存在。")
    incident = [
        edge
        for edge in project.topology.edges.values()
        if vertex_id in {edge.start_vertex_id, edge.end_vertex_id}
    ]
    if len(incident) != 2:
        raise RegionCommandError("多区域结点或非普通顶点不能直接删除。")
    if any(edge.samples or edge.curve for edge in incident):
        raise RegionCommandError("包含采样点或曲线的边暂不能删除中间顶点。")
    edge_ids = {edge.id for edge in incident}
    vertices = dict(project.topology.vertices)
    edges = dict(project.topology.edges)
    half_edges = dict(project.topology.half_edges)
    rings = dict(project.topology.rings)
    endpoint_pairs = []
    old_half_ids = set()
    changed_faces = set()
    for ring_id, ring in project.topology.rings.items():
        half_ids = list(ring.half_edge_ids)
        for index, half_id in enumerate(half_ids):
            next_index = (index + 1) % len(half_ids)
            left = project.topology.half_edges[half_id]
            right = project.topology.half_edges[half_ids[next_index]]
            if left.edge_id not in edge_ids or right.edge_id not in edge_ids:
                continue

            def ends(half):
                edge = project.topology.edges[half.edge_id]
                return (
                    (edge.end_vertex_id, edge.start_vertex_id)
                    if half.reversed
                    else (edge.start_vertex_id, edge.end_vertex_id)
                )

            left_start, left_end = ends(left)
            right_start, right_end = ends(right)
            if left_end != vertex_id or right_start != vertex_id:
                continue
            endpoint_pairs.append(
                (left_start, right_end, ring_id, index, half_id, half_ids[next_index])
            )
            break
    if len(endpoint_pairs) < 1:
        raise RegionCommandError("顶点没有形成可合并的连续边链。")
    edge_by_key = {}
    for edge in edges.values():
        start = vertices[edge.start_vertex_id].point.as_tuple()
        end = vertices[edge.end_vertex_id].point.as_tuple()
        edge_by_key[tuple(sorted((start, end)))] = edge.id
    new_edges = {}
    replacement_edge_id = None
    new_half_edges = {}
    for start_vertex, end_vertex, ring_id, index, left_id, right_id in endpoint_pairs:
        start_point = vertices[start_vertex].point.as_tuple()
        end_point = vertices[end_vertex].point.as_tuple()
        key = tuple(sorted((start_point, end_point)))
        if replacement_edge_id is None:
            replacement_edge_id = edge_by_key.get(key)
            if replacement_edge_id is None:
                merged_edge = Edge(start_vertex_id=start_vertex, end_vertex_id=end_vertex)
                replacement_edge_id = merged_edge.id
                new_edges[merged_edge.id] = merged_edge
        half = HalfEdge(
            edge_id=replacement_edge_id,
            reversed=(
                start_vertex
                != (
                    new_edges.get(replacement_edge_id) or edges[replacement_edge_id]
                ).start_vertex_id
            ),
        )
        new_half_edges[half.id] = half
        ring_half_ids = list(rings[ring_id].half_edge_ids)
        next_index = (index + 1) % len(ring_half_ids)
        if next_index == 0:
            ring_half_ids = [half.id] + ring_half_ids[2:]
        else:
            ring_half_ids[index : next_index + 1] = [half.id]
        rings[ring_id] = replace(rings[ring_id], half_edge_ids=tuple(ring_half_ids))
        old_half_ids.update((left_id, right_id))
        changed_faces.update(
            face.id for face in project.topology.faces.values() if face.outer_ring_id == ring_id
        )
    if replacement_edge_id is None:
        raise RegionCommandError("顶点没有可合并的边。")
    for edge in incident:
        edges.pop(edge.id, None)
    edges.update(new_edges)
    for half_id in old_half_ids:
        half_edges.pop(half_id, None)
    half_edges.update(new_half_edges)
    vertices.pop(vertex_id, None)
    topology = replace(
        project.topology,
        vertices=vertices,
        edges=edges,
        half_edges=half_edges,
        rings=rings,
        geometry_version=project.topology.geometry_version + 1,
    )
    candidate = replace(project, topology=topology, modified_at=datetime.now(UTC))
    for region in candidate.regions.values():
        if not region.visible:
            continue
        for face_id in region.face_ids:
            polygon = candidate.topology.face_polygon(face_id)
            if not polygon.is_valid or polygon.area <= project.tolerances.minimum_face_area:
                raise RegionCommandError("删除顶点会造成无效几何。")
    return candidate
