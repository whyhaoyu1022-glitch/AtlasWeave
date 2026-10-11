"""Executable entry point and lifetime ownership."""

import argparse
import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from atlasweave import __version__
from atlasweave.app.logging_setup import configure_logging
from atlasweave.app.paths import AppPaths
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings
from atlasweave.ui.main_window import MainWindow
from atlasweave.ui.theme import apply_theme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="疆理 AtlasWeave 桌面制图工具")
    parser.add_argument("--version", action="version", version=f"AtlasWeave {__version__}")
    parser.add_argument("--data-dir", type=Path, help="使用独立的设置和日志目录（测试用）")
    parser.add_argument("--smoke-test", action="store_true", help="启动、绘制并关闭窗口（测试用）")
    parser.add_argument("--screenshot", type=Path, help="与 --smoke-test 一起保存真实窗口截图")
    args = parser.parse_args(argv)
    if args.screenshot and not args.smoke_test:
        parser.error("--screenshot 需要 --smoke-test")

    app = QApplication([sys.argv[0]])
    app.setOrganizationName("AtlasWeave")
    app.setApplicationName("AtlasWeave")
    app.setApplicationVersion(__version__)
    try:
        paths = AppPaths.resolve(args.data_dir)
        logger = configure_logging(paths.logs)
    except OSError as exc:
        message = f"无法创建应用日志，请检查用户目录权限和磁盘空间。\n{exc}"
        if args.smoke_test:
            print(message, file=sys.stderr)
        else:
            QMessageBox.critical(None, "启动失败", message)
        return 1

    settings = SettingsStore(paths.config / "settings.json")
    warning = None
    try:
        state = settings.load()
    except SettingsError as exc:
        if args.smoke_test:
            logger.error("Smoke test failed: %s", exc)
            return 1
        state, warning = WindowSettings(), str(exc)
    apply_theme(app)
    window = MainWindow(settings, paths.logs, interactive=not args.smoke_test)
    window.restore_window(state)
    previous_hook = sys.excepthook
    failed = False

    def report_exception(exc_type, value, traceback) -> None:
        nonlocal failed
        logger.critical("Unhandled exception", exc_info=(exc_type, value, traceback))
        if args.smoke_test:
            failed = True
            app.exit(1)
        else:
            QMessageBox.critical(window, "操作未完成", "发生了意外错误。请通过帮助菜单查看日志。")

    sys.excepthook = report_exception

    def finish_smoke_test() -> None:
        nonlocal failed
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(args.screenshot)):
                logger.error("Failed to save screenshot")
                failed = True
        window.close()

    try:
        window.show()
        if warning:
            QTimer.singleShot(0, lambda: QMessageBox.warning(window, "设置读取失败", warning))
        if args.smoke_test:
            QTimer.singleShot(350, finish_smoke_test)
        result = app.exec()
        failed = failed or bool(window.settings_error)
        logger.info("Application stopped | exit=%s", 1 if failed else result)
        return 1 if failed else result
    finally:
        sys.excepthook = previous_hook
        logging.shutdown()
