"""Actionable diagnostics; validation does not modify the project or repair data."""

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING

from shapely import LineString, MultiPolygon, STRtree, union_all
from shapely.errors import GEOSException

if TYPE_CHECKING:
    from atlasweave.domain.project import Project


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    object_id: str | None = None
    path: str = ""
    severity: Severity = Severity.ERROR


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == Severity.ERROR)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def require_valid(self) -> None:
        if not self.is_valid:
            raise ProjectValidationError(self)


class ProjectValidationError(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__("；".join(issue.message for issue in report.errors))


def validate_project(project: "Project", *, allow_skipped_levels: bool = False) -> ValidationReport:
    """Validate references and existing geometry; a draft empty region is legitimate.

    Geometry snapshots are derived on each call, so there is no stale area cache.
    Shared half-edge orientation is checked within a sibling surface, not between
    overlapping parent and child surfaces belonging to different hierarchy levels.
    """
    from atlasweave.domain.region import RegionStatus
    from atlasweave.domain.topology import validate_topology

    tolerance = project.tolerances
    issues = list(validate_topology(project.topology, tolerance).issues)

    def add(code, message, object_id=None, path="", severity=Severity.ERROR):
        issues.append(ValidationIssue(code, message, object_id, path, severity))

    levels = {level.id: level for level in project.hierarchy_levels}
    if not levels:
        add("missing_levels", "工程必须至少有一个行政层级。", project.id, "hierarchy_levels")
    if sorted(level.order for level in project.hierarchy_levels) != list(range(len(levels))):
        add(
            "invalid_level_order", "行政层级必须从 0 开始连续排序。", project.id, "hierarchy_levels"
        )

    seen_ids = {project.id}
    objects = list(project.hierarchy_levels) + list(project.regions.values())
    for collection in project.topology.collections().values():
        objects.extend(collection.values())
    for item in objects:
        if item.id in seen_ids:
            add("duplicate_uuid", "工程中存在重复 UUID。", item.id, "id")
        seen_ids.add(item.id)

    groups = defaultdict(list)
    for key, region in project.regions.items():
        if key != region.id:
            add("mapping_id_mismatch", "区域映射键与对象 ID 不一致。", region.id, "regions")
        level = levels.get(region.level_id)
        if level is None:
            add("missing_level", "区域引用的行政层级不存在。", region.id, "level_id")
        parent = project.regions.get(region.parent_id)
        if level is not None and level.order == 0:
            if region.parent_id is not None:
                add("top_level_parent", "顶级区域不得设置父区域。", region.id, "parent_id")
        elif region.parent_id is None or parent is None:
            add("missing_parent", "非顶级区域必须引用存在的父区域。", region.id, "parent_id")
        if parent is not None and level is not None:
            parent_level = levels.get(parent.level_id)
            if parent_level is not None:
                if parent_level.order >= level.order:
                    add(
                        "invalid_parent_level",
                        "父区域层级必须严格高于子区域。",
                        region.id,
                        "parent_id",
                    )
                elif not allow_skipped_levels and parent_level.order != level.order - 1:
                    add(
                        "skipped_parent_level",
                        "默认只允许相邻上一层的父区域。",
                        region.id,
                        "parent_id",
                    )
        if not region.name.strip():
            add("unnamed_region", "区域尚未命名。", region.id, "name", Severity.WARNING)
        groups[(region.level_id, region.parent_id)].append(region)
        if len(set(region.face_ids)) != len(region.face_ids):
            add("duplicate_face_reference", "区域重复引用同一个面。", region.id, "face_ids")
        for face_id in region.face_ids:
            if face_id not in project.topology.faces:
                add("missing_face", "区域引用的面不存在。", region.id, "face_ids")
        if not region.face_ids and region.status == RegionStatus.VALID:
            add("empty_valid_region", "没有几何的区域应保持草稿状态。", region.id, "status")
        if region.status == RegionStatus.INVALID:
            add("marked_invalid", "区域被标记为无效，需修复后再导出。", region.id, "status")

    # Iterative traversal handles long chains without recursion limits.
    complete = set()
    for start in project.regions:
        trail, positions = [], {}
        current = start
        while current in project.regions and current not in complete:
            if current in positions:
                cycle = trail[positions[current] :]
                add("parent_cycle", "区域父子关系存在循环。", cycle[0], "parent_id")
                break
            positions[current] = len(trail)
            trail.append(current)
            current = project.regions[current].parent_id
        complete.update(trail)

    for vertex in project.topology.vertices.values():
        if not _inside_world(vertex.x, vertex.y, project, tolerance.coordinate_epsilon):
            add("outside_world", "顶点超出有限世界画布。", vertex.id, "position")
    for edge in project.topology.edges.values():
        if any(
            not _inside_world(p.x, p.y, project, tolerance.coordinate_epsilon) for p in edge.samples
        ):
            add("outside_world", "边界采样点超出有限世界画布。", edge.id, "samples")

    geometries = {}
    for region in project.regions.values():
        polygons = []
        for face_id in region.face_ids:
            try:
                polygon = project.topology.face_polygon(face_id)
            except (KeyError, ValueError, GEOSException):
                continue  # Structural issues are already reported, never auto-repaired.
            if (
                polygon.is_valid
                and isfinite(polygon.area)
                and polygon.area > tolerance.minimum_face_area
                and _inside_world(*polygon.bounds[:2], project, tolerance.coordinate_epsilon)
                and _inside_world(*polygon.bounds[2:], project, tolerance.coordinate_epsilon)
            ):
                polygons.append(polygon)
        if len(polygons) == len(region.face_ids) and polygons:
            geometry = MultiPolygon(polygons)
            if not geometry.is_valid:
                add(
                    "invalid_region_parts",
                    "同一区域的多部件几何重叠或不合法。",
                    region.id,
                    "face_ids",
                )
            else:
                geometries[region.id] = geometry
        if len(region.face_ids) > 1:
            add("multipart_region", "区域包含多个部件。", region.id, "face_ids", Severity.WARNING)

    for region in project.regions.values():
        geometry = geometries.get(region.id)
        if geometry is None or region.parent_id is None:
            continue
        parent_geometry = geometries.get(region.parent_id)
        if parent_geometry is None:
            add(
                "parent_geometry_missing",
                "有几何的子区域缺少可校验的父级几何。",
                region.id,
                "parent_id",
            )
        elif not parent_geometry.buffer(tolerance.coordinate_epsilon).covers(geometry):
            add("child_outside_parent", "子区域越出了父区域。", region.id, "face_ids")

    for group in groups.values():
        names = set()
        for region in group:
            name = region.name.strip()
            if name and name in names:
                add(
                    "duplicate_sibling_name",
                    "同父同层区域名称重复。",
                    region.id,
                    "name",
                    Severity.WARNING,
                )
            names.add(name)
        active = [region for region in group if region.id in geometries]
        if not active:
            continue
        shapes = [geometries[region.id] for region in active]
        uses_by_region = {
            region.id: project.topology.edge_uses(region.face_ids) for region in active
        }
        owners_by_edge = defaultdict(set)
        for region_id, uses in uses_by_region.items():
            for edge_id in uses:
                owners_by_edge[edge_id].add(region_id)
        for edge_id, owners in owners_by_edge.items():
            if len(owners) > 2:
                add("non_manifold_edge", "同父同层的一条边不能同时属于三个及以上区域。", edge_id)
        index = STRtree(shapes)
        for i, region in enumerate(active):
            for j in sorted(map(int, index.query(shapes[i], predicate="intersects"))):
                if j <= i:
                    continue
                other = active[j]
                if shapes[i].intersection(shapes[j]).area > tolerance.minimum_face_area:
                    add(
                        "sibling_overlap",
                        f"同级区域“{region.name}”和“{other.name}”存在面积重叠。",
                        region.id,
                    )
                boundary = shapes[i].boundary.intersection(shapes[j].boundary)
                if boundary.length > tolerance.coordinate_epsilon:
                    left = uses_by_region[region.id]
                    right = uses_by_region[other.id]
                    common = set(left) & set(right)
                    shared = union_all(
                        [
                            LineString(
                                [p.as_tuple() for p in project.topology.edge_points(edge_id)]
                            )
                            for edge_id in common
                        ]
                    )
                    if boundary.difference(shared).length > tolerance.coordinate_epsilon:
                        add("unshared_boundary", "相邻区域的公共边界未引用同一份几何。", region.id)
                    for edge_id in sorted(common):
                        if {use[2] for use in left[edge_id]} & {use[2] for use in right[edge_id]}:
                            add(
                                "same_shared_direction",
                                "共享边在相邻区域中的方向必须相反。",
                                edge_id,
                            )
    return ValidationReport(tuple(issues))


def _inside_world(x, y, project, epsilon):
    return (
        -epsilon <= x <= project.world_width_km + epsilon
        and -epsilon <= y <= project.world_height_km + epsilon
    )
