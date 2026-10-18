"""M2 application shell with a real welcome page and project workflow."""

import logging
import platform
import re
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QByteArray, Qt, QUrl
from PyQt6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from atlasweave import APP_NAME, __version__
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings
from atlasweave.domain.hierarchy import HierarchyLevel
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


def _safe_project_filename(name: str) -> str:
    """Return a Windows-safe project stem without silently dropping the name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip())
    cleaned = cleaned.rstrip(" .") or "未命名世界"
    if cleaned.upper() in {"CON", "PRN", "AUX", "NUL"}:
        cleaned = f"_{cleaned}"
    return cleaned[:120]


class NewProjectDialog(QDialog):
    """Four-step M2 project wizard that creates only valid, persisted projects."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建工程向导")
        self.setMinimumSize(720, 520)
        self.setModal(True)
        self.name_edit = QLineEdit("未命名世界")
        self.name_edit.setMaxLength(80)
        self.name_edit.setObjectName("projectNameEdit")
        self.author_edit = QLineEdit()
        self.author_edit.setObjectName("projectAuthorEdit")
        self.description_edit = QLineEdit()
        self.description_edit.setObjectName("projectDescriptionEdit")
        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("projectPathEdit")
        self.browse_button = QPushButton("选择目录")
        self.browse_button.clicked.connect(self._browse_directory)
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(1, 1_000_000)
        self.width_spin.setValue(1200)
        self.width_spin.setSuffix(" km")
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(1, 1_000_000)
        self.height_spin.setValue(800)
        self.height_spin.setSuffix(" km")
        self.path_edit.setText(str(Path.home() / "AtlasWeave"))
        self.levels = ["国家级", "省级", "市级", "县级"]
        self.level_list = QListWidget()
        self.level_list.setObjectName("hierarchyLevelList")
        self.level_list.addItems(self.levels)
        self.level_add = QPushButton("添加层级")
        self.level_remove = QPushButton("删除")
        self.level_up = QPushButton("上移")
        self.level_down = QPushButton("下移")
        self.level_add.clicked.connect(self._add_level)
        self.level_remove.clicked.connect(self._remove_level)
        self.level_up.clicked.connect(lambda: self._move_level(-1))
        self.level_down.clicked.connect(lambda: self._move_level(1))
        self.draw_after_create = QCheckBox("创建后进入国土轮廓绘制准备")
        self.draw_after_create.setChecked(True)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._basic_page())
        self.stack.addWidget(self._world_page())
        self.stack.addWidget(self._levels_page())
        self.stack.addWidget(self._finish_page())
        self.step_label = QLabel("步骤 1/4：基本信息")
        self.back_button = QPushButton("上一步")
        self.next_button = QPushButton("下一步")
        self.create_button = QPushButton("创建工程")
        self.create_button.hide()
        cancel = QPushButton("取消")
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.create_button.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        buttons = QDialogButtonBox()
        buttons.addButton(self.back_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.next_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.create_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        layout = QVBoxLayout(self)
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)
        layout.addWidget(buttons)
        self._update_navigation()

    def _basic_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.addRow("工程名称", self.name_edit)
        form.addRow("作者", self.author_edit)
        form.addRow("说明", self.description_edit)
        path_row = QWidget()
        row = QVBoxLayout(path_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.path_edit)
        row.addWidget(self.browse_button)
        form.addRow("保存目录", path_row)
        return page

    def _world_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        presets = QWidget()
        p = QVBoxLayout(presets)
        p.setContentsMargins(0, 0, 0, 0)
        for label, width, height in (
            ("大陆 1200×800", 1200, 800),
            ("大洲 2400×1600", 2400, 1600),
            ("行星 40000×20000", 40000, 20000),
            ("自定义", None, None),
        ):
            button = QPushButton(label)
            p.addWidget(button)
            if width:
                button.clicked.connect(
                    lambda _, w=width, h=height: (
                        self.width_spin.setValue(w),
                        self.height_spin.setValue(h),
                    )
                )
        form.addRow("尺寸预设", presets)
        form.addRow("世界宽度", self.width_spin)
        form.addRow("世界高度", self.height_spin)
        return page

    def _levels_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("至少保留一层，最多十层，层级名称不得重复。"))
        layout.addWidget(self.level_list)
        row = QHBoxLayout()
        for button in (self.level_add, self.level_remove, self.level_up, self.level_down):
            row.addWidget(button)
        layout.addLayout(row)
        return page

    def _finish_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("请确认工程信息。"))
        layout.addWidget(self.summary)
        layout.addWidget(self.draw_after_create)
        return page

    def _browse_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择工程目录", self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def _add_level(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "添加层级", "层级名称")
        if (
            ok
            and name.strip()
            and name.strip()
            not in [self.level_list.item(i).text() for i in range(self.level_list.count())]
            and self.level_list.count() < 10
        ):
            self.level_list.addItem(name.strip())

    def _remove_level(self) -> None:
        if self.level_list.count() > 1 and self.level_list.currentRow() >= 0:
            self.level_list.takeItem(self.level_list.currentRow())

    def _move_level(self, delta: int) -> None:
        row = self.level_list.currentRow()
        target = row + delta
        if 0 <= row < self.level_list.count() and 0 <= target < self.level_list.count():
            item = self.level_list.takeItem(row)
            self.level_list.insertItem(target, item)
            self.level_list.setCurrentRow(target)

    def _next(self) -> None:
        if self.stack.currentIndex() == 0 and not self.name_edit.text().strip():
            QMessageBox.warning(self, "名称不能为空", "请输入工程名称。")
            return
        if self.stack.currentIndex() == 2:
            names = [self.level_list.item(i).text().strip() for i in range(self.level_list.count())]
            if not names or any(not n for n in names) or len(set(names)) != len(names):
                QMessageBox.warning(self, "层级无效", "层级名称不能为空且不能重复。")
                return
        self.stack.setCurrentIndex(self.stack.currentIndex() + 1)
        if self.stack.currentIndex() == 3:
            self._refresh_summary()
        self._update_navigation()

    def _back(self) -> None:
        if self.stack.currentIndex() > 0:
            self.stack.setCurrentIndex(self.stack.currentIndex() - 1)
        self._update_navigation()

    def _accept(self) -> None:
        names = self.levels_from_ui()
        if not self.name_edit.text().strip() or not names or len(set(names)) != len(names):
            QMessageBox.warning(self, "信息无效", "请检查工程名称和行政层级名称。")
            return
        self.accept()

    def _update_navigation(self) -> None:
        index = self.stack.currentIndex()
        self.step_label.setText(
            f"步骤 {index + 1}/4：{('基本信息', '世界尺寸', '行政层级', '完成')[index]}"
        )
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(index < 3)
        self.create_button.setVisible(index == 3)

    def _refresh_summary(self) -> None:
        levels = "、".join(self.level_list.item(i).text() for i in range(self.level_list.count()))
        summary = (
            f"工程：{self.name_edit.text().strip()}\n"
            f"作者：{self.author_edit.text().strip() or '未填写'}\n"
            f"世界：{self.width_spin.value():g} × {self.height_spin.value():g} km\n"
            f"层级：{levels}\n文件：{self.target_path()}"
        )
        self.summary.setText(summary)

    def target_path(self) -> Path:
        return (
            Path(self.path_edit.text()).expanduser()
            / f"{_safe_project_filename(self.name_edit.text())}.atlasweave"
        )

    def project(self) -> Project:
        return Project(
            name=self.name_edit.text().strip(),
            description=self.description_edit.text().strip(),
            world_width_km=self.width_spin.value(),
            world_height_km=self.height_spin.value(),
            hierarchy_levels=tuple(
                HierarchyLevel(order=i, name=name, default_type_name=name.rstrip("级"))
                for i, name in enumerate(self.levels_from_ui())
            ),
            metadata={"author": self.author_edit.text().strip()},
        )

    def levels_from_ui(self) -> list[str]:
        return [self.level_list.item(i).text().strip() for i in range(self.level_list.count())]


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
            project = dialog.project()
            target = dialog.target_path()
            try:
                result = self.store.save(project, target)
            except ProjectIOError as exc:
                self._show_io_message("无法创建工程", f"{exc}\n请检查保存目录权限。", exc=exc)
                return
            self.project = project
            self.project_path = result.path
            self.loaded_project = LoadedProject(
                project=project, path=result.path, validation=result.validation
            )
            self.dirty = False
            self._show_project_workspace()
            warning = self._touch_recent()
            if warning:
                self._show_io_message("工程已创建，请留意", warning, warning=True)
            self.statusBar().showMessage(f"已创建：{self.project_path}", 5000)

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
