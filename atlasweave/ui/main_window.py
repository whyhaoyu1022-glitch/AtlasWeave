"""M2 application shell with a real welcome page and project workflow."""

import logging
import platform
from pathlib import Path

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QByteArray, Qt, QUrl
from PyQt6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from atlasweave import APP_NAME, __version__
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings
from atlasweave.domain.project import Project
from atlasweave.services.project_io import ProjectIOError, ProjectStore, RecentProjects

logger = logging.getLogger(__name__)


class NewProjectDialog(QDialog):
    """Collect the minimum real project data required by the M2 workflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建工程")
        self.setModal(True)
        self.name_edit = QLineEdit("未命名世界")
        self.name_edit.setObjectName("projectNameEdit")
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(1, 1_000_000)
        self.width_spin.setValue(1200)
        self.width_spin.setSuffix(" km")
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(1, 1_000_000)
        self.height_spin.setValue(800)
        self.height_spin.setSuffix(" km")
        form = QFormLayout()
        form.addRow("工程名称", self.name_edit)
        form.addRow("世界宽度", self.width_spin)
        form.addRow("世界高度", self.height_spin)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("创建一个可保存的空白疆域工程。"))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "名称不能为空", "请输入工程名称。")
            return
        self.accept()

    def project(self) -> Project:
        return Project(
            name=self.name_edit.text().strip(),
            world_width_km=self.width_spin.value(),
            world_height_km=self.height_spin.value(),
        )


class WelcomePage(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("welcomePage")
        self.new_button = QPushButton("新建工程")
        self.new_button.setObjectName("newProjectButton")
        self.open_button = QPushButton("打开工程")
        self.open_button.setObjectName("openProjectButton")
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentProjectsList")
        self.recent_list.setAccessibleName("最近工程")
        title = QLabel(APP_NAME)
        title.setObjectName("welcomeTitle")
        subtitle = QLabel("创建或打开一个疆域工程，开始构建你的世界。")
        subtitle.setObjectName("welcomeSubtitle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(72, 56, 72, 56)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(20)
        layout.addWidget(self.new_button)
        layout.addWidget(self.open_button)
        layout.addSpacing(16)
        layout.addWidget(QLabel("最近工程"))
        layout.addWidget(self.recent_list, 1)


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
        self.store = ProjectStore()
        self.recent = RecentProjects(log_directory.parent / "config" / "recent-projects.json")
        self.project: Project | None = None
        self.project_path: Path | None = None
        self.dirty = False
        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        self.statusBar().setMinimumHeight(24)
        self._create_menus()
        self._show_welcome()

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        self.new_action = QAction("新建工程(&N)", self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(self.new_project)
        file_menu.addAction(self.new_action)
        self.open_action = QAction("打开工程(&O)", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_project)
        file_menu.addAction(self.open_action)
        self.save_action = QAction("保存(&S)", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_project)
        self.save_action.setEnabled(False)
        file_menu.addAction(self.save_action)
        self.save_as_action = QAction("另存为(&A)", self)
        self.save_as_action.triggered.connect(self.save_project_as)
        self.save_as_action.setEnabled(False)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
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

    def _show_welcome(self) -> None:
        page = WelcomePage(self)
        page.new_button.clicked.connect(self.new_project)
        page.open_button.clicked.connect(self.open_project)
        for recent in self.recent.load():
            item = QListWidgetItem(str(recent.path))
            item.setData(Qt.ItemDataRole.UserRole, str(recent.path))
            page.recent_list.addItem(item)
        page.recent_list.itemDoubleClicked.connect(self._open_recent_item)
        self.setCentralWidget(page)
        self.statusBar().showMessage("就绪")
        self._update_actions()

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        self._load_project(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _update_actions(self) -> None:
        has_project = self.project is not None
        self.save_action.setEnabled(has_project)
        self.save_as_action.setEnabled(has_project)
        title = (
            APP_NAME
            if self.project is None
            else f"{self.project.name}{' *' if self.dirty else ''} - {APP_NAME}"
        )
        self.setWindowTitle(title)

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        dialog = NewProjectDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.project = dialog.project()
            self.project_path = None
            self.dirty = True
            self._show_project_workspace()

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开工程", "", "疆理工程 (*.atlasweave)")
        if path:
            self._load_project(Path(path))

    def _load_project(self, path: Path) -> None:
        try:
            loaded = self.store.load(path)
        except ProjectIOError as exc:
            QMessageBox.critical(self, "无法打开工程", str(exc))
            return
        self.project = loaded.project
        self.project_path = loaded.path
        self.dirty = False
        self.recent.touch(loaded.path)
        self._show_project_workspace()
        if loaded.warning:
            QMessageBox.warning(self, "工程以只读方式打开", loaded.warning)

    def save_project(self) -> bool:
        if self.project is None:
            return False
        if self.project_path is None:
            return self.save_project_as()
        return self._save_to(self.project_path)

    def save_project_as(self) -> bool:
        if self.project is None:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "保存工程", "", "疆理工程 (*.atlasweave)")
        if not path:
            return False
        target = Path(path)
        if target.suffix.lower() != ".atlasweave":
            target = target.with_suffix(".atlasweave")
        return self._save_to(target)

    def _save_to(self, path: Path) -> bool:
        try:
            self.store.save(self.project, path)
        except ProjectIOError as exc:
            QMessageBox.critical(self, "无法保存工程", str(exc))
            return False
        self.project_path = path.resolve()
        self.dirty = False
        self.recent.touch(self.project_path)
        self._update_actions()
        self.statusBar().showMessage(f"已保存：{self.project_path}")
        return True

    def _show_project_workspace(self) -> None:
        workspace = QFrame()
        workspace.setObjectName("projectWorkspace")
        layout = QVBoxLayout(workspace)
        label = QLabel(f"{self.project.name if self.project else ''}\n地图工作区")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("workspaceTitle")
        layout.addWidget(label)
        self.setCentralWidget(workspace)
        self.statusBar().showMessage("新工程已创建，保存后即可继续编辑")
        self._update_actions()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "工程尚未保存",
            "当前工程有未保存修改，是否保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self.save_project()
        return choice == QMessageBox.StandardButton.Discard

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
            f"{APP_NAME}\n版本 {__version__}\n构建：M2 开发版\n\n"
            f"Python {platform.python_version()}\nPyQt {PYQT_VERSION_STR} / Qt {QT_VERSION_STR}\n\n"
            "工程格式：.atlasweave\n依赖：PyQt6 / Qt 6 / PyQt6-sip",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        try:
            self.settings.save(WindowSettings(geometry=bytes(self.saveGeometry())))
        except SettingsError as exc:
            self.settings_error = str(exc)
            if self.interactive:
                QMessageBox.warning(self, "设置未保存", str(exc))
        logger.info("Window closed")
        super().closeEvent(event)
