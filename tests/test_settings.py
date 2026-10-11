import base64
import json

import pytest

from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings


def test_settings_round_trip_preserves_unknown_fields(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 1, "future": {"theme": "新主题"}}), encoding="utf-8")
    store = SettingsStore(path)
    assert store.load() == WindowSettings()
    state = WindowSettings(geometry=b"\x00\xffQt geometry")
    store.save(state)
    assert SettingsStore(path).load() == state
    assert json.loads(path.read_text(encoding="utf-8"))["future"] == {"theme": "新主题"}


@pytest.mark.parametrize(
    "content", ["{broken", "[]", '{"version": 999}', '{"version": 1, "window_geometry": "%%%"}']
)
def test_invalid_settings_are_not_overwritten(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    store = SettingsStore(path)
    with pytest.raises(SettingsError):
        store.load()
    with pytest.raises(SettingsError):
        store.save(WindowSettings())
    assert path.read_text(encoding="utf-8") == content


def test_failed_replace_preserves_previous_settings(tmp_path, monkeypatch):
    path = tmp_path / "config" / "settings.json"
    store = SettingsStore(path)
    store.save(WindowSettings(b"original"))
    before = path.read_bytes()

    def disk_error(*args):
        raise OSError("simulated full disk")

    monkeypatch.setattr("atlasweave.app.settings.os.replace", disk_error)
    with pytest.raises(SettingsError):
        store.save(WindowSettings(b"new"))
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".settings-*.tmp"))
    assert json.loads(before)["window_geometry"] == base64.b64encode(b"original").decode()
