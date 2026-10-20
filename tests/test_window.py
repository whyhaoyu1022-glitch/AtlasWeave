from PyQt6.QtWidgets import QListWidget, QMessageBox
from shapely.geometry import Polygon

from atlasweave import APP_NAME
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings
from atlasweave.domain.project import Project
from atlasweave.ui.main_window import MainWindow


def test_window_close_and_reopen_restores_size(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    window = MainWindow(SettingsStore(path), tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.resize(1280, 800)
    qtbot.waitUntil(lambda: window.isVisible())
    assert window.windowTitle() == APP_NAME
    assert window.minimumWidth() == 1180
    assert window.statusBar().currentMessage() == "就绪"
    window.exit_action.trigger()
    assert not window.isVisible()
    reopened = MainWindow(SettingsStore(path), tmp_path)
    qtbot.addWidget(reopened)
    reopened.restore_window(SettingsStore(path).load())
    reopened.show()
    assert reopened.width() == 1280
    assert reopened.height() == 800


def test_about_and_log_failure_are_accessible(qtbot, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "about", lambda *args: messages.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args))
    monkeypatch.setattr("atlasweave.ui.main_window.QDesktopServices.openUrl", lambda url: False)
    window = MainWindow(SettingsStore(tmp_path / "settings.json"), tmp_path)
    qtbot.addWidget(window)
    window.about_action.trigger()
    assert "Python" in messages[0][2] and "M2" in messages[0][2]
    window.logs_action.trigger()
    assert str(tmp_path) in messages[1][2]


def test_bad_geometry_uses_default_size(qtbot, tmp_path):
    window = MainWindow(SettingsStore(tmp_path / "settings.json"), tmp_path)
    qtbot.addWidget(window)
    window.restore_window(WindowSettings(b"invalid"))
    assert window.width() == 1440


def test_noninteractive_close_reports_settings_failure(qtbot, tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")

    def fail_save(state):
        raise SettingsError("磁盘不可写")

    monkeypatch.setattr(store, "save", fail_save)
    window = MainWindow(store, tmp_path, interactive=False)
    qtbot.addWidget(window)
    window.show()
    window.close()
    assert window.settings_error == "磁盘不可写"


def test_welcome_page_exposes_real_project_actions(qtbot, tmp_path):
    window = MainWindow(SettingsStore(tmp_path / "settings.json"), tmp_path)
    qtbot.addWidget(window)
    assert window.centralWidget().objectName() == "welcomePage"
    assert window.centralWidget().findChild(QListWidget) is not None
    assert window.new_action.isEnabled()
    assert window.open_action.isEnabled()
    assert not window.save_action.isEnabled()


def test_save_as_adds_atlasweave_suffix_and_updates_recent(qtbot, tmp_path, monkeypatch):
    window = MainWindow(SettingsStore(tmp_path / "settings.json"), tmp_path)
    qtbot.addWidget(window)
    window.project = Project(name="测试")
    window.dirty = True
    monkeypatch.setattr(
        "atlasweave.ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(tmp_path / "test-project"), ""),
    )
    assert window.save_project_as()
    saved = tmp_path / "test-project.atlasweave"
    assert saved.is_file()
    assert window.project_path == saved.resolve()
    assert not window.dirty
    assert window.recent.load()[0].path == saved.resolve()


def test_project_workspace_exposes_real_docks_and_tree(qtbot, tmp_path):
    window = MainWindow(SettingsStore(tmp_path / "settings.json"), tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.project = Project(name="导航测试")
    window._show_project_workspace()
    assert window.project_dock.objectName() == "projectDock"
    assert window.property_dock.objectName() == "propertyDock"
    assert window.bottom_dock.objectName() == "bottomDock"
    assert window.project_dock.isVisible()
    assert window.project_tree.topLevelItem(0).text(0) == "导航测试"
    assert window.project_tree.topLevelItem(0).childCount() == 1
    assert window.draw_action.isEnabled()
    window.project_dock.hide()
    window.reset_workspace_layout()
    assert window.project_dock.isVisible()
    assert not window.bottom_dock.isVisible()


def test_commit_drawn_polygon_creates_real_topology_and_dirty_state(qtbot, tmp_path):
    window = MainWindow(SettingsStore(tmp_path / "settings.json"), tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.project = Project(name="绘制测试")
    window._show_project_workspace()

    polygon = Polygon(((10, 10), (40, 10), (25, 40)))
    window._commit_region(polygon, "三角区域", "国家")

    assert window.dirty
    assert len(window.project.regions) == 1
    region = next(iter(window.project.regions.values()))
    assert region.name == "三角区域"
    assert len(window.project.topology.faces) == 1
    assert window.canvas.selected_region_ids == {region.id}
    window.dirty = False
    window.close()
