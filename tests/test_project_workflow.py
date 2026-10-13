"""Exercise real UI file actions and failure recovery, including future data."""

import json
import sys
import zipfile
from dataclasses import replace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox

from atlasweave.app.settings import SettingsStore
from atlasweave.domain.project import Project
from atlasweave.services.project_io import ProjectStore
from atlasweave.ui.main_window import MainWindow

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows native Qt modal lifecycle is covered by the manual acceptance flow; "
    "the same deterministic workflow runs in Linux offscreen CI.",
)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    result = MainWindow(
        SettingsStore(tmp_path / "config" / "settings.json"), tmp_path / "logs", interactive=False
    )
    qtbot.addWidget(result)
    result.notices = []
    monkeypatch.setattr(
        result, "_show_io_message", lambda *args, **kwargs: result.notices.append((args, kwargs))
    )
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    yield result
    result.dirty = False
    result.interactive = False
    result.close()
    result.deleteLater()


def extended_file(tmp_path, *, version="1.0.0"):
    store = ProjectStore()
    path = tmp_path / "with-extensions.atlasweave"
    project = Project(
        name="保留未来数据",
        extra_fields={"future_project": {"enabled": True}},
        map_style={"future_style": [1, 2]},
        extensions={"future_module": {"revision": 12}},
    )
    project = replace(
        project, topology=replace(project.topology, extra_fields={"future_graph": 17})
    )
    store.save(
        project,
        path,
        attachments={"attachments/photo.bin": b"\x00\x01\xff"},
        extra_members={"extensions/plugin.bin": b"\xff\x00", "future/file.txt": b"KEEP"},
        document_extra={"future_root": {"nested": [True, "preserve"]}},
    )
    if version != "1.0.0":
        with zipfile.ZipFile(path) as archive:
            data = {name: archive.read(name) for name in archive.namelist()}
        root = json.loads(data["project.json"])
        root["format_version"] = version
        data["project.json"] = json.dumps(root).encode()
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in data.items():
                archive.writestr(name, content)
    return path


def documents(path):
    with zipfile.ZipFile(path) as archive:
        return {
            name: json.loads(archive.read(name)) if name.endswith(".json") else archive.read(name)
            for name in archive.namelist()
            if name != "thumbnail.png"
        }


def test_ui_open_save_as_and_save_preserve_all_extension_data(window, tmp_path, monkeypatch):
    source = extended_file(tmp_path)
    original = documents(source)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(source), ""))
    window.open_action.trigger()
    assert window.loaded_project.document_extra["future_root"]["nested"][0]
    window.save_action.trigger()
    assert documents(source) == original

    copy = tmp_path / "copy.atlasweave"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(copy), ""))
    window.save_as_action.trigger()
    assert window.project_path == copy.resolve()
    assert documents(copy) == original
    # The next ordinary save must retain the envelope of the newly saved copy too.
    window.project = replace(window.project, name="修改名称")
    window.dirty = True
    window.save_action.trigger()
    assert not window.dirty
    saved = documents(copy)
    assert saved["project.json"]["project"].pop("name") == "修改名称"
    original["project.json"]["project"].pop("name")
    assert saved == original
    assert not window.notices


def test_future_version_disables_ui_save_and_save_as_without_creating_copy(
    window, tmp_path, monkeypatch
):
    source = extended_file(tmp_path, version="9.0.0")
    original = source.read_bytes()
    assert window._load_project(source)
    assert window.read_only
    assert not window.save_action.isEnabled() and not window.save_as_action.isEnabled()
    assert "只读" in window.statusBar().currentMessage()

    def must_not_pick(*args, **kwargs):
        pytest.fail("Read-only save must not offer a destination dialog")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", must_not_pick)
    assert not window.save_project()
    assert not window.save_project_as()
    assert not window._save_to(tmp_path / "copy.atlasweave")
    assert source.read_bytes() == original
    assert not (tmp_path / "copy.atlasweave").exists()
    assert any("无损" in args[1] for args, kwargs in window.notices)


def test_failed_atomic_save_keeps_file_path_and_dirty_state(window, tmp_path, monkeypatch):
    source = extended_file(tmp_path)
    window._load_project(source)
    original = source.read_bytes()
    window.project = replace(window.project, name="不能丢失的修改")
    window.dirty = True
    loaded_before = window.loaded_project

    def fail_replace(source, target):
        raise OSError("disk full")

    monkeypatch.setattr("atlasweave.services.project_io.os.replace", fail_replace)
    assert not window.save_project()
    assert source.read_bytes() == original
    assert window.dirty and window.project.name == "不能丢失的修改"
    assert window.project_path == source.resolve()
    assert window.loaded_project is loaded_before
    assert "内存" in window.notices[-1][0][1]
    assert not list(tmp_path.glob(".with-extensions.atlasweave.*.tmp"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Save)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and window.dirty


def test_failed_save_as_retains_original_project_path_and_envelope(window, tmp_path, monkeypatch):
    source = extended_file(tmp_path)
    window._load_project(source)
    loaded_before = window.loaded_project
    window.dirty = True
    parent = tmp_path / "not-a-directory"
    parent.write_text("blocked")
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **kw: (str(parent / "copy.atlasweave"), "")
    )
    assert not window.save_project_as()
    assert window.project_path == source.resolve()
    assert window.loaded_project is loaded_before and window.dirty


def test_backup_failure_warns_but_main_save_succeeds(window, tmp_path, monkeypatch):
    source = extended_file(tmp_path)
    window._load_project(source)
    window.project = replace(window.project, name="主保存成功")
    window.dirty = True

    def fail_backup(*args, **kwargs):
        raise OSError("backup locked")

    monkeypatch.setattr("atlasweave.services.project_io.shutil.copy2", fail_backup)
    assert window.save_project()
    assert ProjectStore().load(source).project.name == "主保存成功"
    assert not window.dirty
    assert "备份" in window.notices[-1][0][1]
    assert window.notices[-1][1]["warning"]


@pytest.mark.parametrize("action", ["open", "save"])
def test_recent_history_failure_does_not_fail_primary_operation(
    window, tmp_path, monkeypatch, action
):
    source = extended_file(tmp_path)
    window._load_project(source)
    window.notices.clear()

    def fail_history(*args, **kwargs):
        raise OSError("config directory read-only")

    monkeypatch.setattr(window.recent, "touch", fail_history)
    if action == "open":
        assert window._load_project(source)
    else:
        window.project = replace(window.project, name="保存成功，历史失败")
        window.dirty = True
        assert window.save_project()
        assert ProjectStore().load(source).project.name == window.project.name
    assert not window.dirty
    assert window.project_path == source.resolve()
    assert "工程不受影响" in window.notices[-1][0][1]
    assert window.notices[-1][1]["warning"]


def test_failed_open_preserves_current_unsaved_project(window, tmp_path):
    window.project = Project(name="当前工程")
    window.dirty = True
    before = window.project
    assert not window._load_project(tmp_path / "missing.atlasweave")
    assert window.project is before and window.dirty
    assert window.project_path is None
    assert "备份" in window.notices[-1][0][1]


def test_recent_open_respects_cancel_of_unsaved_changes(window, tmp_path, monkeypatch):
    source = extended_file(tmp_path)
    window.project = Project(name="待保存")
    window.dirty = True
    item = QListWidgetItem(str(source))
    item.setData(Qt.ItemDataRole.UserRole, str(source))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    window._open_recent_item(item)
    assert window.project.name == "待保存" and window.dirty


@pytest.mark.parametrize(
    "choice", [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save]
)
def test_close_keeps_unsaved_project_on_cancel_or_cancelled_save_as(window, monkeypatch, choice):
    window.project = Project(name="尚未保存")
    window.dirty = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: choice)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: ("", ""))
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and window.dirty
    assert not window.settings.path.exists()


def test_save_as_confirms_final_suffixed_destination_with_cancel_default(
    window, tmp_path, monkeypatch
):
    source = extended_file(tmp_path)
    window.project = Project(name="另一个工程")
    window.dirty = True
    original = source.read_bytes()
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **kw: (str(source.with_suffix("")), "")
    )
    calls = []

    def decline(*args, **kwargs):
        calls.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    assert not window.save_project_as()
    assert calls[0][-1] == QMessageBox.StandardButton.No
    assert str(source) in calls[0][2]
    assert source.read_bytes() == original and window.dirty


def test_io_error_message_provides_log_location_and_exception_details(window, monkeypatch):
    captured = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: captured.append(box))
    # Call the production helper, bypassing the fixture's nonblocking notice recorder.
    MainWindow._show_io_message(window, "保存失败", "请检查磁盘空间。", exc=OSError("disk full"))
    assert "OSError" in captured[0].detailedText()
    assert str(window.log_directory) in captured[0].detailedText()
    assert captured[0].textFormat() == Qt.TextFormat.PlainText
