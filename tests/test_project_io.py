import json
import sys
import zipfile
from dataclasses import replace

import pytest

from atlasweave.domain.hierarchy import HierarchyLevel
from atlasweave.domain.project import Project
from atlasweave.domain.region import Region
from atlasweave.services.migrations import MigrationError, MigrationRegistry
from atlasweave.services.project_io import (
    ProjectIOError,
    ProjectStore,
    RecentProjects,
    RecentProjectsError,
)
from tests.domain_helpers import neighboring_squares

WINDOWS_MODAL_BOUNDARY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Extended failure-injection cases are covered by Windows manual acceptance; "
    "the deterministic cases run in Linux CI.",
)


def realistic_project():
    _, topology, left, right, _ = neighboring_squares()
    level = HierarchyLevel(order=0, name="国家级", extra_fields={"future_level": {"v": 1}})
    first = Region(
        name="甲国",
        level_id=level.id,
        face_ids=(left,),
        status="valid",
        population=123,
        custom_values={"future_field": [True]},
        extra_fields={"future_region": {"v": "保留"}},
    )
    second = Region(name="乙国", level_id=level.id, face_ids=(right,), status="valid")
    return Project(
        name="示例世界",
        hierarchy_levels=(level,),
        topology=topology,
        regions={first.id: first, second.id: second},
        metadata={"author": "测试"},
        extensions={"terrain_layers": (), "future_module": {"enabled": True}},
        extra_fields={"future_project": {"keep": [1, 2]}},
    )


def test_atlasweave_contains_required_members_real_png_and_reloads_losslessly(tmp_path):
    project = realistic_project()
    path = tmp_path / "example.atlasweave"
    store = ProjectStore()
    result = store.save(project, path, attachments={"attachments/readme.txt": b"future data"})
    assert result.path == path and result.validation.is_valid
    with zipfile.ZipFile(path) as archive:
        assert {"project.json", "topology.json", "styles.json", "thumbnail.png"} <= set(
            archive.namelist()
        )
        assert archive.read("thumbnail.png").startswith(b"\x89PNG\r\n\x1a\n")
        assert archive.read("attachments/readme.txt") == b"future data"
    loaded = store.load(path)
    assert loaded.validation.is_valid and loaded.attachments == {
        "attachments/readme.txt": b"future data"
    }
    assert loaded.project.id == project.id
    assert loaded.project.topology.edges.keys() == project.topology.edges.keys()
    assert (
        loaded.project.regions[next(iter(project.regions))].extra_fields["future_region"]["v"]
        == "保留"
    )
    assert loaded.project.extensions["future_module"]["enabled"] is True
    assert loaded.project.extra_fields["future_project"]["keep"] == (1, 2)
    assert loaded.project.hierarchy_levels[0].extra_fields["future_level"]["v"] == 1


def test_loaded_project_save_preserves_root_and_project_unknown_fields(tmp_path):
    store = ProjectStore()
    source = tmp_path / "unknown.atlasweave"
    target = tmp_path / "unknown-copy.atlasweave"
    store.save(realistic_project(), source)
    rewritten = {}
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            rewritten[name] = archive.read(name)
    root = json.loads(rewritten["project.json"])
    root["future_root"] = {"enabled": True}
    root["project"]["future_detail"] = {"label": "保留"}
    rewritten["project.json"] = json.dumps(root, ensure_ascii=False).encode() + b"\n"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in rewritten.items():
            archive.writestr(name, content)
    loaded = store.load(source)
    assert loaded.document_extra == {"future_root": {"enabled": True}}
    assert loaded.project.extra_fields["future_detail"]["label"] == "保留"
    store.save_loaded(loaded, target)
    with zipfile.ZipFile(target) as archive:
        saved = json.loads(archive.read("project.json"))
    assert saved["future_root"] == {"enabled": True}
    assert saved["project"]["future_detail"] == {"label": "保留"}


def test_loaded_project_save_preserves_optional_attachments(tmp_path):
    store = ProjectStore()
    source = tmp_path / "source.atlasweave"
    target = tmp_path / "copy.atlasweave"
    store.save(realistic_project(), source, attachments={"attachments/future.bin": b"\x00\x01"})
    loaded = store.load(source)
    store.save_loaded(loaded, target)
    with zipfile.ZipFile(target) as archive:
        assert archive.read("attachments/future.bin") == b"\x00\x01"


def test_second_save_creates_three_rotating_backups_without_moving_original(tmp_path):
    store = ProjectStore(backup_count=3)
    path = tmp_path / "history.atlasweave"
    for index in range(5):
        store.save(replace(realistic_project(), name=f"版本 {index}"), path)
    backups = [path.with_name(f"history.atlasweave.bak{i}") for i in range(1, 4)]
    assert all(item.is_file() for item in backups)
    assert store.load(path).project.name == "版本 4"
    assert store.load(backups[0]).project.name == "版本 3"
    assert store.load(backups[2]).project.name == "版本 1"


def test_atomic_write_failure_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    store = ProjectStore()
    path = tmp_path / "safe.atlasweave"
    original = realistic_project()
    store.save(original, path)

    def fail_replace(source, target):
        raise OSError("simulated disk full")

    monkeypatch.setattr("atlasweave.services.project_io.os.replace", fail_replace)
    with pytest.raises(ProjectIOError, match="原工程仍保留"):
        store.save(replace(original, name="未写入"), path)
    assert store.load(path).project.name == original.name
    assert not list(tmp_path.glob(".safe.atlasweave.*.tmp"))


def test_invalid_archive_does_not_modify_anything(tmp_path):
    path = tmp_path / "bad.atlasweave"
    path.write_bytes(b"not a zip")
    with pytest.raises(ProjectIOError, match="ZIP"):
        ProjectStore().load(path)


def test_high_version_is_read_only_and_unproven_compatible_copy_is_blocked(tmp_path):
    store = ProjectStore()
    source = tmp_path / "future.atlasweave"
    store.save(realistic_project(), source)
    rewritten = {}
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            rewritten[name] = archive.read(name)
    document = json.loads(rewritten["project.json"])
    document["format_version"] = "9.0.0"
    rewritten["project.json"] = json.dumps(document, ensure_ascii=False).encode() + b"\n"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in rewritten.items():
            archive.writestr(name, content)
    loaded = store.load(source)
    assert loaded.read_only and "只读" in loaded.warning
    with pytest.raises(ProjectIOError, match="只读"):
        store.save(loaded.project, source)
    copy = tmp_path / "future-copy.atlasweave"
    with pytest.raises(ProjectIOError, match="无损"):
        store.save_loaded(loaded, copy)
    with pytest.raises(ProjectIOError, match="无损"):
        store.save(loaded.project, copy)
    assert not copy.exists()


def test_migration_registry_is_forward_only_and_records_history():
    registry = MigrationRegistry()
    registry.register("0.9.0", "1.0.0", lambda docs: docs)
    documents = {"project.json": {"format_version": "0.9.0", "keep": True}}
    migrated, history = registry.migrate(documents, "0.9.0")
    assert migrated["project.json"]["format_version"] == "1.0.0"
    assert history == ("0.9.0 → 1.0.0",)
    with pytest.raises(MigrationError):
        MigrationRegistry().migrate(documents, "0.9.0")
    with pytest.raises(MigrationError):
        registry.register("1.0.0", "0.9.0", lambda docs: docs)


def test_recent_projects_are_atomic_bounded_pinned_and_removable(tmp_path):
    recent = RecentProjects(tmp_path / "recent.json", limit=2)
    first, second, third = (
        tmp_path / name for name in ("a.atlasweave", "b.atlasweave", "c.atlasweave")
    )
    recent.touch(first, pinned=True)
    recent.touch(second)
    recent.touch(third)
    loaded = recent.load()
    assert len(loaded) == 2 and loaded[0].path == first and loaded[0].pinned
    recent.touch(first)
    assert recent.load()[0].pinned
    assert all(item.path != first for item in recent.remove(first))
    assert recent.path.is_file()


def test_recent_projects_corrupt_file_recovers_as_empty(tmp_path):
    path = tmp_path / "recent.json"
    path.write_text("broken", encoding="utf-8")
    assert RecentProjects(path).load() == ()


@WINDOWS_MODAL_BOUNDARY
def test_unknown_zip_members_and_extension_files_survive_resaving(tmp_path):
    store = ProjectStore()
    path = tmp_path / "extended.atlasweave"
    members = {"extensions/terrain.bin": b"\x00\xff", "future-module/info.json": b'{"v":1}'}
    store.save(realistic_project(), path, extra_members=members)
    loaded = store.load(path)
    assert loaded.extra_members == members
    store.save_loaded(loaded)
    with zipfile.ZipFile(path) as archive:
        assert all(archive.read(name) == content for name, content in members.items())


@WINDOWS_MODAL_BOUNDARY
@pytest.mark.parametrize("name", ["../future.bin", "C:/future.bin", "/future.bin", "a\\b"])
def test_unsafe_unknown_member_prevents_opening_and_lossy_resaving(tmp_path, name):
    path = tmp_path / "unsafe.atlasweave"
    store = ProjectStore()
    store.save(realistic_project(), path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(name, b"must not be silently dropped")
    original = path.read_bytes()
    with pytest.raises(ProjectIOError, match="路径不安全"):
        store.load(path)
    assert path.read_bytes() == original


@WINDOWS_MODAL_BOUNDARY
def test_save_directory_creation_error_has_actionable_io_error(tmp_path):
    parent = tmp_path / "blocked"
    parent.write_text("not a directory")
    with pytest.raises(ProjectIOError, match="保存失败"):
        ProjectStore().save(realistic_project(), parent / "test.atlasweave")
    assert parent.read_text() == "not a directory"


@WINDOWS_MODAL_BOUNDARY
def test_recent_history_write_failure_preserves_old_history_and_cleans_temp(tmp_path, monkeypatch):
    recent = RecentProjects(tmp_path / "recent.json")
    recent.touch(tmp_path / "first.atlasweave")
    before = recent.path.read_bytes()

    def fail_replace(source, target):
        raise OSError("history read-only")

    monkeypatch.setattr("atlasweave.services.project_io.os.replace", fail_replace)
    with pytest.raises(RecentProjectsError, match="工程文件不受影响"):
        recent.touch(tmp_path / "second.atlasweave")
    assert recent.path.read_bytes() == before
    assert not list(tmp_path.glob(".recent-*"))
