from dataclasses import replace

import pytest

from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region
from atlasweave.domain.validation import ProjectValidationError, Severity, validate_project
from atlasweave.domain.values import new_id


def issue_codes(project, **options):
    return {item.code for item in validate_project(project, **options).issues}


def test_four_level_draft_tree_is_valid():
    levels = tuple(HierarchyLevel(order=i, name=f"第{i}层") for i in range(4))
    regions = []
    for level in levels:
        regions.append(
            Region(
                name=f"区域{level.order}",
                level_id=level.id,
                parent_id=regions[-1].id if regions else None,
            )
        )
    project = Project(hierarchy_levels=levels, regions={r.id: r for r in regions})
    assert validate_project(project).is_valid


def test_missing_or_noncontiguous_levels_and_duplicate_ids_are_reported():
    assert "missing_levels" in issue_codes(Project(hierarchy_levels=()))
    level = HierarchyLevel(order=1, name="国家级")
    assert "invalid_level_order" in issue_codes(Project(hierarchy_levels=(level,)))
    duplicate = replace(level, order=0)
    assert "duplicate_uuid" in issue_codes(Project(hierarchy_levels=(duplicate, level)))


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"level_id": new_id()}, "missing_level"),
        ({"parent_id": new_id()}, "missing_parent"),
        ({"parent_id": None}, "missing_parent"),
    ],
)
def test_invalid_references_are_reported(change, expected):
    level = HierarchyLevel(order=1, name="省级")
    region = Region(name="北省", level_id=level.id, parent_id=new_id())
    region = replace(region, **change)
    project = Project(
        hierarchy_levels=(HierarchyLevel(order=0, name="国家级"), level),
        regions={region.id: region},
    )
    assert expected in issue_codes(project)


def test_parent_cycles_terminate_and_raise_actionable_report():
    level = HierarchyLevel(order=0, name="国家级")
    left = Region(name="甲", level_id=level.id)
    right = Region(name="乙", level_id=level.id, parent_id=left.id)
    left = replace(left, parent_id=right.id)
    project = Project(hierarchy_levels=(level,), regions={left.id: left, right.id: right})
    report = validate_project(project)
    assert "parent_cycle" in {item.code for item in report.errors}
    with pytest.raises(ProjectValidationError) as caught:
        report.require_valid()
    assert caught.value.report is report


def test_skipped_levels_require_explicit_advanced_policy():
    levels = tuple(HierarchyLevel(order=i, name=f"第{i}层") for i in range(3))
    parent = Region(name="国家", level_id=levels[0].id)
    child = Region(name="城市", level_id=levels[2].id, parent_id=parent.id)
    project = Project(hierarchy_levels=levels, regions={r.id: r for r in (parent, child)})
    assert "skipped_parent_level" in issue_codes(project)
    assert validate_project(project, allow_skipped_levels=True).is_valid
    bad = replace(child, parent_id=child.id)
    invalid = replace(project, regions={parent.id: parent, bad.id: bad})
    assert "invalid_parent_level" in issue_codes(invalid, allow_skipped_levels=True)


def test_deleting_referenced_level_or_parent_never_leaves_valid_project():
    top = HierarchyLevel(order=0, name="国家级")
    sub = HierarchyLevel(order=1, name="省级")
    parent = Region(name="国家", level_id=top.id)
    child = Region(name="省", level_id=sub.id, parent_id=parent.id)
    project = Project(hierarchy_levels=(top, sub), regions={r.id: r for r in (parent, child)})
    assert validate_project(project).is_valid
    assert "missing_level" in issue_codes(replace(project, hierarchy_levels=(top,)))
    assert "missing_parent" in issue_codes(replace(project, regions={child.id: child}))


def test_duplicate_names_warn_without_using_names_as_reference_keys():
    level = HierarchyLevel(order=0, name="国家级")
    left = Region(name="同名", level_id=level.id)
    right = Region(name="同名", level_id=level.id)
    project = Project(hierarchy_levels=(level,), regions={r.id: r for r in (left, right)})
    report = validate_project(project)
    assert report.is_valid
    warning = next(item for item in report.issues if item.code == "duplicate_sibling_name")
    assert warning.severity == Severity.WARNING
    assert left.id != right.id


def test_mapping_keys_and_cross_namespace_uuid_collisions_are_detected():
    level = HierarchyLevel(order=0, name="国家级")
    region = Region(name="国家", level_id=level.id)
    project = Project(id=region.id, hierarchy_levels=(level,), regions={new_id(): region})
    assert {"mapping_id_mismatch", "duplicate_uuid"} <= issue_codes(project)
