"""M2 application shell with a real welcome page and project workflow."""

import logging
import platform
from dataclasses import replace
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
    QSlider,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from atlasweave import APP_NAME, __version__
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings
from atlasweave.domain.project import Project
from atlasweave.services.project_io import (
    LoadedProject,
    ProjectIOError,
    ProjectStore,
    RecentProjects,
    RecentProjectsError,
)
from atlasweave.ui.map_canvas import MapCanvas

logger = logging.getLogger(__name__)


class NewProjectDialog(QDialog):
    """Collect the minimum real project data required by the M2 workflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建工程")
        self.setModal(True)
        self.name_edit = QLineEdit("未命名世界")
        self.name_edit.setMaxLength(80)
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
        self.recent = RecentProjects(settings.path.parent / "recent-projects.json")
        self.loaded_project: LoadedProject | None = None
        self.project: Project | None = None
        self.project_path: Path | None = None
        self.dirty = False
        self.canvas = MapCanvas(self)
        self.canvas.coordinate_changed.connect(self._update_coordinate_status)
        self.canvas.zoom_changed.connect(self._update_zoom_status)
        self.canvas.hide()
        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        self.statusBar().setMinimumHeight(24)
        self._create_menus()
        self._create_navigation_status()
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
        view_menu = self.menuBar().addMenu("视图(&V)")
        self.zoom_in_action = QAction("放大", self)
        self.zoom_in_action.setShortcut(QKeySequence("Ctrl++"))
        self.zoom_in_action.triggered.connect(lambda: self.canvas.zoom_by(1.15))
        view_menu.addAction(self.zoom_in_action)
        self.zoom_out_action = QAction("缩小", self)
        self.zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        self.zoom_out_action.triggered.connect(lambda: self.canvas.zoom_by(1 / 1.15))
        view_menu.addAction(self.zoom_out_action)
        self.fit_world_action = QAction("适应世界", self)
        self.fit_world_action.setShortcut(QKeySequence("Ctrl+0"))
        self.fit_world_action.triggered.connect(self.canvas.fit_world)
        view_menu.addAction(self.fit_world_action)
        view_menu.addSeparator()
        self.pan_action = QAction("平移工具", self)
        self.pan_action.setCheckable(True)
        self.pan_action.toggled.connect(self.canvas.set_pan_mode)
        view_menu.addAction(self.pan_action)

        self.navigation_toolbar = QToolBar("地图导航", self)
        self.navigation_toolbar.setObjectName("navigationToolbar")
        self.navigation_toolbar.addAction(self.pan_action)
        self.navigation_toolbar.addSeparator()
        self.navigation_toolbar.addAction(self.zoom_out_action)
        self.navigation_toolbar.addAction(self.zoom_in_action)
        self.navigation_toolbar.addAction(self.fit_world_action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.navigation_toolbar)
        self.navigation_toolbar.hide()

    def _create_navigation_status(self) -> None:
        self.coordinate_status = QLabel("X —  Y —")
        self.coordinate_status.setObjectName("worldCoordinateStatus")
        self.zoom_status = QLabel("缩放 —")
        self.zoom_status.setObjectName("zoomStatus")
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setObjectName("zoomSlider")
        self.zoom_slider.setRange(int(MapCanvas.MIN_ZOOM_PERCENT), int(MapCanvas.MAX_ZOOM_PERCENT))
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(140)
        self.zoom_slider.valueChanged.connect(self.canvas.set_zoom_percent)
        self.statusBar().addPermanentWidget(self.coordinate_status)
        self.statusBar().addPermanentWidget(self.zoom_status)
        self.statusBar().addPermanentWidget(self.zoom_slider)

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
        self.navigation_toolbar.hide()
        self.canvas.set_pan_mode(False)
        self.pan_action.setChecked(False)
        self.coordinate_status.setText("X —  Y —")
        self.zoom_status.setText("缩放 —")
        self.statusBar().showMessage("就绪")
        self._update_actions()

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        if self._confirm_discard():
            self._load_project(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _update_actions(self) -> None:
        has_project = self.project is not None
        writable = has_project and not self.read_only
        self.save_action.setEnabled(writable)
        self.save_as_action.setEnabled(writable)
        title = (
            APP_NAME
            if self.project is None
            else f"{self.project.name}{'*' if self.dirty else ''} - {APP_NAME}"
        )
        self.setWindowTitle(title)
        for action in (self.zoom_in_action, self.zoom_out_action, self.fit_world_action):
            action.setEnabled(has_project)
        self.pan_action.setEnabled(has_project)

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        dialog = NewProjectDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.project = dialog.project()
            self.project_path = None
            self.loaded_project = None
            self.dirty = True
            self._show_project_workspace()

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开工程", "", "疆理工程 (*.atlasweave)")
        if path:
            self._load_project(Path(path))

    @property
    def read_only(self) -> bool:
        return bool(self.loaded_project and self.loaded_project.read_only)

    def _load_project(self, path: Path) -> bool:
        try:
            loaded = self.store.load(path)
        except ProjectIOError as exc:
            logger.exception("工程打开失败：%s", path)
            self._show_io_message(
                "无法打开工程", f"{exc}\n请检查文件位置、格式，或尝试打开备份。", exc=exc
            )
            return False
        self.loaded_project = loaded
        self.project = loaded.project
        self.project_path = loaded.path
        self.dirty = False
        self._show_project_workspace()
        warnings = [message for message in (loaded.warning, self._touch_recent()) if message]
        if warnings:
            self._show_io_message("工程已打开，请留意", "\n".join(warnings), warning=True)
        return True

    def save_project(self) -> bool:
        if self.project is None:
            return False
        if not self._can_save():
            return False
        if self.project_path is None:
            return self.save_project_as()
        return self._save_to(self.project_path)

    def save_project_as(self) -> bool:
        if self.project is None:
            return False
        if not self._can_save():
            return False
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存工程",
            "",
            "疆理工程 (*.atlasweave)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return False
        target = Path(path)
        if target.suffix.lower() != ".atlasweave":
            target = target.with_suffix(".atlasweave")
        if target.exists():
            choice = QMessageBox.question(
                self,
                "确认覆盖工程",
                f"{target}\n已存在，是否覆盖？原文件会按备份设置保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return False
        return self._save_to(target)

    def _can_save(self) -> bool:
        if self.read_only:
            self._show_io_message(
                "工程只读，无法保存",
                "此工程由更高版本创建，当前无法保证兼容转换无损。请使用创建此工程的版本编辑。",
                warning=True,
            )
            return False
        return True

    def _save_to(self, path: Path) -> bool:
        if self.project is None or not self._can_save():
            return False
        try:
            if self.loaded_project is not None:
                snapshot = replace(self.loaded_project, project=self.project)
                result = self.store.save_loaded(snapshot, path)
            else:
                result = self.store.save(self.project, path)
        except ProjectIOError as exc:
            logger.exception("工程保存失败：%s", path)
            self._show_io_message(
                "无法保存工程",
                f"{exc}\n当前修改仍在内存中，请检查目录权限和磁盘空间，或另存到其他位置。",
                exc=exc,
            )
            return False
        self.project_path = result.path
        if self.loaded_project is not None:
            self.loaded_project = replace(
                self.loaded_project,
                project=self.project,
                path=result.path,
                validation=result.validation,
            )
        else:
            self.loaded_project = LoadedProject(
                project=self.project, path=result.path, validation=result.validation
            )
        self.dirty = False
        self._update_actions()
        self.statusBar().showMessage(f"已保存：{self.project_path}", 5000)
        warnings = [message for message in (result.warning, self._touch_recent()) if message]
        if warnings:
            self._show_io_message("工程已保存，请留意", "\n".join(warnings), warning=True)
        return True

    def _touch_recent(self) -> str | None:
        if self.project_path is None:
            return None
        try:
            self.recent.touch(self.project_path)
        except (RecentProjectsError, OSError) as exc:
            logger.exception("更新最近工程失败：%s", self.project_path)
            return (
                "最近工程记录未能更新，已打开或保存的工程不受影响。"
                f"\n{exc}\n请检查设置目录权限和磁盘空间；下次可以通过“打开工程”选择文件。"
            )
        return None

    def _show_io_message(
        self, title: str, message: str, *, exc: Exception | None = None, warning: bool = False
    ) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Critical)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message)
        details = f"日志目录：{self.log_directory}"
        if exc is not None:
            details = f"{type(exc).__name__}: {exc}\n{details}"
        box.setDetailedText(details)
        box.exec()

    def _show_project_workspace(self) -> None:
        workspace = QFrame()
        workspace.setObjectName("projectWorkspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        if self.project is not None:
            self.canvas.set_world(
                self.project.world_width_km,
                self.project.world_height_km,
                self.project.map_style,
            )
        layout.addWidget(self.canvas)
        self.canvas.show()
        self.setCentralWidget(workspace)
        self.navigation_toolbar.show()
        if self.read_only:
            self.statusBar().showMessage("只读工程：当前版本无法保证兼容转换无损")
        elif self.dirty:
            self.statusBar().showMessage("工程尚未保存")
        else:
            self.statusBar().showMessage(f"已打开：{self.project_path}")
        self._update_actions()

    def _update_coordinate_status(self, x: float, y: float, inside: bool) -> None:
        if inside:
            self.coordinate_status.setText(f"X {x:.2f} km  Y {y:.2f} km")
        else:
            self.coordinate_status.setText("X —  Y —")

    def _update_zoom_status(self, percent: float) -> None:
        self.zoom_status.setText(f"缩放 {percent:.0f}%")
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(round(percent))
        self.zoom_slider.blockSignals(False)

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
            QMessageBox.StandardButton.Cancel,
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
