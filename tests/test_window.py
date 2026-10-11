from PyQt6.QtWidgets import QMessageBox

from atlasweave import APP_NAME
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings
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
    assert "Python" in messages[0][2] and "M0" in messages[0][2]
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
