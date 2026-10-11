"""One shared boundary graph with directed ring references (§4.6).

Transform helpers create candidates, not user commands. Callers must validate the
candidate in project context before committing it through the command system.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from math import hypot, isfinite

from shapely import LinearRing, Polygon
from shapely.errors import GEOSException
from shapely.validation import explain_validity

from atlasweave.domain.geometry import Point, Tolerances
from atlasweave.domain.validation import ValidationIssue, ValidationReport
from atlasweave.domain.values import (
    ModelError,
    boolean_value,
    integer_value,
    json_object,
    new_id,
    number_value,
    object_map,
    uuid_sequence,
    uuid_value,
)


@dataclass(frozen=True, kw_only=True)
class Vertex:
    x: float
    y: float
    id: str = field(default_factory=new_id)
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid_value(self.id, "id")
        object.__setattr__(self, "x", number_value(self.x, "x"))
        object.__setattr__(self, "y", number_value(self.y, "y"))
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))

    @property
    def point(self) -> Point:
        return Point(self.x, self.y)


@dataclass(frozen=True, kw_only=True)
class Edge:
    start_vertex_id: str
    end_vertex_id: str
    id: str = field(default_factory=new_id)
    samples: tuple[Point, ...] = ()
    curve: Mapping = field(default_factory=dict)
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "start_vertex_id", "end_vertex_id"):
            uuid_value(getattr(self, name), name)
        if not isinstance(self.samples, (list, tuple)) or any(
            not isinstance(p, Point) for p in self.samples
        ):
            raise ModelError("samples", "必须是中间世界坐标点的序列")
        object.__setattr__(self, "samples", tuple(self.samples))
        object.__setattr__(self, "curve", json_object(self.curve, "curve"))
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))


@dataclass(frozen=True, kw_only=True)
class HalfEdge:
    edge_id: str
    reversed: bool = False
    id: str = field(default_factory=new_id)
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid_value(self.id, "id")
        uuid_value(self.edge_id, "edge_id")
        boolean_value(self.reversed, "reversed")
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))


@dataclass(frozen=True, kw_only=True)
class Ring:
    half_edge_ids: tuple[str, ...]
    id: str = field(default_factory=new_id)
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid_value(self.id, "id")
        object.__setattr__(
            self, "half_edge_ids", uuid_sequence(self.half_edge_ids, "half_edge_ids")
        )
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))


@dataclass(frozen=True, kw_only=True)
class Face:
    outer_ring_id: str
    inner_ring_ids: tuple[str, ...] = ()
    id: str = field(default_factory=new_id)
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        uuid_value(self.id, "id")
        uuid_value(self.outer_ring_id, "outer_ring_id")
        object.__setattr__(
            self, "inner_ring_ids", uuid_sequence(self.inner_ring_ids, "inner_ring_ids")
        )
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))


@dataclass(frozen=True, kw_only=True)
class Topology:
    vertices: Mapping[str, Vertex] = field(default_factory=dict)
    edges: Mapping[str, Edge] = field(default_factory=dict)
    half_edges: Mapping[str, HalfEdge] = field(default_factory=dict)
    rings: Mapping[str, Ring] = field(default_factory=dict)
    faces: Mapping[str, Face] = field(default_factory=dict)
    geometry_version: int = 0
    extra_fields: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, model in (
            ("vertices", Vertex),
            ("edges", Edge),
            ("half_edges", HalfEdge),
            ("rings", Ring),
            ("faces", Face),
        ):
            object.__setattr__(self, name, object_map(getattr(self, name), model, name))
        integer_value(self.geometry_version, "geometry_version")
        object.__setattr__(self, "extra_fields", json_object(self.extra_fields, "extra_fields"))

    def collections(self) -> dict:
        return {
            name: getattr(self, name)
            for name in ("vertices", "edges", "half_edges", "rings", "faces")
        }

    def edge_points(self, edge_id: str) -> tuple[Point, ...]:
        edge = self.edges[edge_id]
        if edge.curve:
            raise ModelError("curve", "当前基础拓扑尚不支持曲线求值，不得静默替换为直线")
        return (
            self.vertices[edge.start_vertex_id].point,
            *edge.samples,
            self.vertices[edge.end_vertex_id].point,
        )

    def ring_points(self, ring_id: str) -> tuple[Point, ...]:
        ring = self.rings[ring_id]
        if not ring.half_edge_ids:
            raise ModelError("half_edge_ids", "环没有边界")
        points = []
        first_vertex = previous_vertex = None
        for half_id in ring.half_edge_ids:
            half = self.half_edges[half_id]
            edge = self.edges[half.edge_id]
            start, end = edge.start_vertex_id, edge.end_vertex_id
            segment = self.edge_points(half.edge_id)
            if half.reversed:
                start, end, segment = end, start, tuple(reversed(segment))
            if first_vertex is None:
                first_vertex = start
            elif previous_vertex != start:
                raise ModelError("half_edge_ids", "环内相邻边的拓扑节点不连接")
            points.extend(segment[:-1])
            previous_vertex = end
        if previous_vertex != first_vertex:
            raise ModelError("half_edge_ids", "环未闭合")
        points.append(points[0])
        return tuple(points)

    def face_polygon(self, face_id: str) -> Polygon:
        face = self.faces[face_id]
        outer = [p.as_tuple() for p in self.ring_points(face.outer_ring_id)]
        inner = [
            [p.as_tuple() for p in self.ring_points(ring_id)] for ring_id in face.inner_ring_ids
        ]
        return Polygon(outer, inner)

    def edge_uses(self, face_ids) -> dict:
        result = defaultdict(list)
        for face_id in face_ids:
            face = self.faces[face_id]
            for ring_id in (face.outer_ring_id, *face.inner_ring_ids):
                for half_id in self.rings[ring_id].half_edge_ids:
                    half = self.half_edges[half_id]
                    result[half.edge_id].append((face_id, half_id, half.reversed))
        return dict(result)

    def adjacency(self, face_ids) -> dict[str, frozenset[str]]:
        """Derive edge adjacency for a caller-selected sibling surface."""
        result = {face_id: set() for face_id in face_ids}
        for uses in self.edge_uses(tuple(result)).values():
            for left, _, direction in uses:
                for right, _, other_direction in uses:
                    if left != right and direction != other_direction:
                        result[left].add(right)
        return {face_id: frozenset(neighbors) for face_id, neighbors in result.items()}

    def with_vertex_position(self, vertex_id: str, point: Point) -> "Topology":
        vertices = dict(self.vertices)
        vertices[vertex_id] = replace(vertices[vertex_id], x=point.x, y=point.y)
        return replace(self, vertices=vertices, geometry_version=self.geometry_version + 1)

    def with_edge_samples(self, edge_id: str, samples: tuple[Point, ...]) -> "Topology":
        edges = dict(self.edges)
        edges[edge_id] = replace(edges[edge_id], samples=samples)
        return replace(self, edges=edges, geometry_version=self.geometry_version + 1)


def validate_topology(topology: Topology, tolerance: Tolerances) -> ValidationReport:
    issues = []

    def add(code, message, object_id, path=""):
        issues.append(ValidationIssue(code, message, object_id, path))

    for name, objects in topology.collections().items():
        for key, item in objects.items():
            if key != item.id:
                add("mapping_id_mismatch", "拓扑映射键与对象 ID 不一致。", item.id, name)
    coordinates = set()
    for vertex in topology.vertices.values():
        if vertex.point in coordinates:
            add("duplicate_vertex_coordinate", "同一坐标应引用同一个拓扑顶点。", vertex.id)
        coordinates.add(vertex.point)
    geometries = set()
    for edge_key, edge in topology.edges.items():
        if (
            edge.start_vertex_id not in topology.vertices
            or edge.end_vertex_id not in topology.vertices
        ):
            add("missing_vertex", "边引用的顶点不存在。", edge.id)
            continue
        if edge.start_vertex_id == edge.end_vertex_id:
            add("edge_self_loop", "边的两个端点不得是同一个节点。", edge.id)
        if edge.curve:
            add("unsupported_curve", "基础拓扑尚不支持曲线求值。", edge.id, "curve")
            continue
        points = topology.edge_points(edge_key)
        if any(
            hypot(a.x - b.x, a.y - b.y) < tolerance.minimum_edge_length
            for a, b in zip(points, points[1:], strict=False)
        ):
            add("short_edge", "边包含近似零长度的线段。", edge.id)
        canonical = min(
            tuple(p.as_tuple() for p in points), tuple(p.as_tuple() for p in reversed(points))
        )
        if canonical in geometries:
            add("duplicate_edge_geometry", "相同的边界几何应使用同一个 Edge。", edge.id)
        geometries.add(canonical)
    for half in topology.half_edges.values():
        if half.edge_id not in topology.edges:
            add("missing_edge", "半边引用的边不存在。", half.id, "edge_id")
    rings = {}
    for ring_key, ring in topology.rings.items():
        if len(set(ring.half_edge_ids)) != len(ring.half_edge_ids):
            add("duplicate_half_edge_reference", "环重复引用同一条半边。", ring.id)
        try:
            points = topology.ring_points(ring_key)
        except KeyError:
            add("missing_ring_reference", "环引用的半边、边或顶点不存在。", ring.id)
            continue
        except ValueError as exc:
            add("invalid_ring", f"环几何无效：{exc}", ring.id)
            continue
        if len(set(points[:-1])) < 3:
            add("too_few_ring_vertices", "环至少需要三个不同坐标点。", ring.id)
            continue
        try:
            shape = LinearRing([p.as_tuple() for p in points])
        except GEOSException:
            add("invalid_ring_geometry", "无法求值环几何。", ring.id)
            continue
        if not shape.is_simple:
            add("self_intersection", "环存在自相交。", ring.id)
        rings[ring_key] = shape
    for face_key, face in topology.faces.items():
        if face.outer_ring_id not in rings or any(
            ring_id not in rings for ring_id in face.inner_ring_ids
        ):
            add("missing_face_ring", "面的环不存在或无法求值。", face.id)
            continue
        if not rings[face.outer_ring_id].is_ccw:
            add("outer_ring_direction", "外环必须采用逆时针方向。", face.id)
        if any(rings[ring_id].is_ccw for ring_id in face.inner_ring_ids):
            add("inner_ring_direction", "内环必须采用顺时针方向。", face.id)
        try:
            polygon = topology.face_polygon(face_key)
        except (ValueError, GEOSException):
            add("invalid_face_geometry", "无法求值面几何。", face.id)
            continue
        if not polygon.is_valid:
            add("invalid_face", f"面几何不合法：{explain_validity(polygon)}", face.id)
        if not isfinite(polygon.area):
            add("nonfinite_face_area", "面的面积超出有限数值范围。", face.id)
        elif polygon.area <= tolerance.minimum_face_area:
            add("small_face", "面的面积为零或小于工程容差。", face.id)
    return ValidationReport(tuple(issues))
