"""M0 application shell; map editing arrives in later milestones."""

import logging
import platform
from pathlib import Path

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QByteArray, Qt, QUrl
from PyQt6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import QFrame, QLabel, QMainWindow, QMessageBox, QVBoxLayout

from atlasweave import APP_NAME, __version__
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: SettingsStore,
        log_directory: Path,
        *,
        interactive: bool = True,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.log_directory = log_directory
        self.interactive = interactive
        self.settings_error: str | None = None
        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        workspace = QFrame()
        workspace.setObjectName("emptyWorkspace")
        workspace.setAccessibleName("地图工作区")
        layout = QVBoxLayout(workspace)
        title = QLabel(APP_NAME)
        title.setObjectName("workspaceTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        self.setCentralWidget(workspace)
        self.statusBar().setMinimumHeight(24)
        self.statusBar().showMessage("就绪")
        self._create_menus()

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        self.exit_action = QAction("退出(&X)", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.setStatusTip("关闭疆理 AtlasWeave")
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)
        help_menu = self.menuBar().addMenu("帮助(&H)")
        self.logs_action = QAction("打开日志目录(&L)", self)
        self.logs_action.triggered.connect(self.open_log_directory)
        help_menu.addAction(self.logs_action)
        self.about_action = QAction("关于疆理(&A)", self)
        self.about_action.triggered.connect(self.show_about)
        help_menu.addAction(self.about_action)

    def restore_window(self, state: WindowSettings) -> None:
        if state.geometry and not self.restoreGeometry(QByteArray(state.geometry)):
            logger.warning("Invalid window geometry; using default layout")

    def open_log_directory(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_directory.resolve()))):
            logger.warning("Could not open log directory")
            QMessageBox.warning(
                self, "无法打开目录", f"请在文件管理器中打开：\n{self.log_directory}"
            )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于疆理",
            f"{APP_NAME}\n版本 {__version__}\n构建：M0 开发版\n\n"
            f"Python {platform.python_version()}\nPyQt {PYQT_VERSION_STR} / Qt {QT_VERSION_STR}\n\n"
            "工程格式：尚未启用\n依赖：PyQt6 / Qt 6 / PyQt6-sip\n"
            "此阶段仅提供应用骨架，地图编辑将按开发里程碑交付。",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self.settings.save(WindowSettings(geometry=bytes(self.saveGeometry())))
        except SettingsError as exc:
            self.settings_error = str(exc)
            if self.interactive:
                QMessageBox.warning(self, "设置未保存", str(exc))
        logger.info("Window closed")
        super().closeEvent(event)
