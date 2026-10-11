import os
import subprocess
import sys


def run_app(*args):
    return subprocess.run(
        [sys.executable, "-m", "atlasweave", *map(str, args)],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_complete_startup_render_and_clean_exit(tmp_path):
    screenshot = tmp_path / "window.png"
    result = run_app("--smoke-test", "--data-dir", tmp_path, "--screenshot", screenshot)
    assert result.returncode == 0, result.stdout + result.stderr
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    log = (tmp_path / "logs" / "atlasweave.log").read_text(encoding="utf-8")
    assert "Application starting" in log
    assert "Window closed" in log
    assert "Application stopped | exit=0" in log
    assert (tmp_path / "config" / "settings.json").exists()


def test_startup_with_unwritable_log_path_fails_without_hanging(tmp_path):
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
    assert run_app("--smoke-test", "--data-dir", tmp_path).returncode == 1


def test_corrupt_settings_fail_smoke_test_without_overwriting(tmp_path):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "settings.json"
    path.write_text("broken", encoding="utf-8")
    assert run_app("--smoke-test", "--data-dir", tmp_path).returncode == 1
    assert path.read_text(encoding="utf-8") == "broken"
