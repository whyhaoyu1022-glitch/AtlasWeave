"""M2 application shell with a real welcome page and project workflow."""

import logging
import platform
import re
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QByteArray, Qt, QUrl
from PyQt6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence, QUndoStack
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
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
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shapely.errors import GEOSException
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

from atlasweave import APP_NAME, __version__
from atlasweave.app.settings import SettingsError, SettingsStore, WindowSettings
from atlasweave.commands.project_commands import ProjectSnapshotCommand
from atlasweave.commands.region_commands import (
    RegionCommandError,
    add_region_component,
    create_top_level_region,
    merge_sibling_regions,
    polygon_from_freehand,
    preview_region_split,
    region_adjacency,
    split_region_into_children,
)
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


class RegionCreateDialog(QDialog):
    """Collect the minimum metadata before committing a drawn region."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("创建顶级区域")
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("regionNameEdit")
        self.type_edit = QLineEdit("区域")
        self.type_edit.setObjectName("regionTypeEdit")
        form = QFormLayout(self)
        form.addRow("区域名称", self.name_edit)
        form.addRow("区域类型", self.type_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "名称不能为空", "请输入区域名称。")
            return
        if not self.type_edit.text().strip():
            QMessageBox.warning(self, "类型不能为空", "请输入区域类型。")
            return
        self.accept()

    @property
    def region_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def type_name(self) -> str:
        return self.type_edit.text().strip()


class RegionSplitDialog(QDialog):
    """Show a cut summary and collect names for every resulting child region."""

    def __init__(
        self,
        parent_name: str,
        parent: QWidget | None = None,
        *,
        piece_count: int = 2,
        minimum_area: float | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"切分区域：{parent_name}")
        self.setMinimumWidth(440)
        self.piece_count = piece_count
        self.name_edits = []
        self.type_edit = QLineEdit("省")
        form = QFormLayout(self)
        summary = f"预览将创建 {piece_count} 个子区域"
        if minimum_area is not None:
            summary += f"；最小面积 {minimum_area:.6g} km²"
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        form.addRow("切分预览", summary_label)
        for index in range(piece_count):
            edit = QLineEdit(f"{parent_name} - 新区域 {index + 1}")
            edit.setObjectName(f"splitRegionNameEdit{index + 1}")
            self.name_edits.append(edit)
            form.addRow(f"第 {index + 1} 个子区域", edit)
        form.addRow("区域类型", self.type_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        names = self.names
        if any(not name for name in names):
            QMessageBox.warning(self, "名称不能为空", "请输入所有子区域名称。")
            return
        if len(set(names)) != len(names):
            QMessageBox.warning(self, "名称重复", "子区域名称不能重复。")
            return
        if not self.type_name:
            QMessageBox.warning(self, "类型不能为空", "请输入子区域类型。")
            return
        self.accept()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(edit.text().strip() for edit in self.name_edits)

    @property
    def first_name(self) -> str:
        return self.names[0]

    @property
    def second_name(self) -> str:
        return self.names[1]

    @property
    def type_name(self) -> str:
        return self.type_edit.text().strip()


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
        self._has_restored_dock_state = False
        self.store = ProjectStore()
        self.recent = RecentProjects(settings.path.parent / "recent-projects.json")
        self.loaded_project: LoadedProject | None = None
        self.project: Project | None = None
        self.project_path: Path | None = None
        self.dirty = False
        self.undo_stack = QUndoStack(self)
        self.undo_stack.indexChanged.connect(self._history_index_changed)
        self.canvas = MapCanvas(self)
        self.canvas.coordinate_changed.connect(self._update_coordinate_status)
        self.canvas.zoom_changed.connect(self._update_zoom_status)
        self.canvas.selection_changed.connect(self._canvas_selection_changed)
        self.canvas.polygon_completed.connect(self._polygon_completed)
        self.canvas.freehand_completed.connect(self._freehand_completed)
        self.canvas.split_completed.connect(self._split_completed)
        self.canvas.hide()
        self._create_docks()
        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        self.statusBar().setMinimumHeight(24)
        self._create_menus()
        self._create_navigation_status()
        self._show_welcome()

    def _create_docks(self) -> None:
        """Create the real M2 workspace panels; content is refreshed per project."""
        self.project_tree = QTreeWidget()
        self.project_tree.setObjectName("projectTree")
        self.project_tree.setHeaderLabels(["名称", "类型"])
        self.project_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.project_tree.currentItemChanged.connect(self._project_tree_changed)
        layers = QListWidget()
        layers.setObjectName("layerList")
        layers.addItem("基础地图")
        left_tabs = QTabWidget()
        left_tabs.setObjectName("leftPanelTabs")
        left_tabs.addTab(self.project_tree, "区划树")
        left_tabs.addTab(layers, "图层")
        self.project_dock = QDockWidget("工程与区划", self)
        self.project_dock.setObjectName("projectDock")
        self.project_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.project_dock.setWidget(left_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)

        self.property_label = QLabel("未选择工程")
        self.property_label.setObjectName("propertyPanelContent")
        self.property_label.setWordWrap(True)
        property_page = QWidget()
        property_layout = QVBoxLayout(property_page)
        property_layout.addWidget(self.property_label)
        property_layout.addStretch(1)
        style_page = QLabel("当前工程样式将随样式编辑功能接入。")
        style_page.setWordWrap(True)
        right_tabs = QTabWidget()
        right_tabs.setObjectName("rightPanelTabs")
        right_tabs.addTab(property_page, "属性")
        right_tabs.addTab(style_page, "样式")
        self.property_dock = QDockWidget("属性与样式", self)
        self.property_dock.setObjectName("propertyDock")
        self.property_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.property_dock.setWidget(right_tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.property_dock)

        self.issue_panel = QPlainTextEdit()
        self.issue_panel.setObjectName("issuePanel")
        self.issue_panel.setReadOnly(True)
        self.issue_panel.setPlainText("当前没有校验问题。")
        self.region_table = QTableWidget(0, 3)
        self.region_table.setObjectName("regionTable")
        self.region_table.setHorizontalHeaderLabels(["名称", "层级", "状态"])
        self.operation_log = QPlainTextEdit()
        self.operation_log.setObjectName("operationLog")
        self.operation_log.setReadOnly(True)
        bottom_tabs = QTabWidget()
        bottom_tabs.setObjectName("bottomPanelTabs")
        bottom_tabs.addTab(self.issue_panel, "问题")
        bottom_tabs.addTab(self.region_table, "区域数据")
        bottom_tabs.addTab(self.operation_log, "操作日志")
        self.bottom_dock = QDockWidget("问题与数据", self)
        self.bottom_dock.setObjectName("bottomDock")
        self.bottom_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.bottom_dock.setWidget(bottom_tabs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.bottom_dock)
        self._dock_widgets = (self.project_dock, self.property_dock, self.bottom_dock)
        self._default_dock_state = bytes(self.saveState(1))
        for dock in self._dock_widgets:
            dock.hide()
        self.bottom_dock.hide()

    def _create_panel_actions(self, view_menu) -> None:
        panel_menu = view_menu.addMenu("面板")
        for dock in self._dock_widgets:
            panel_menu.addAction(dock.toggleViewAction())
        panel_menu.addSeparator()
        self.reset_workspace_action = QAction("重置工作区布局", self)
        self.reset_workspace_action.triggered.connect(self.reset_workspace_layout)
        panel_menu.addAction(self.reset_workspace_action)

    def reset_workspace_layout(self) -> None:
        self.restoreState(QByteArray(self._default_dock_state), 1)
        self._set_workspace_docks_visible(True)
        self.bottom_dock.hide()
        self.statusBar().showMessage("工作区布局已重置", 4000)

    def _set_workspace_docks_visible(self, visible: bool) -> None:
        self.project_dock.setVisible(visible)
        self.property_dock.setVisible(visible)
        if not visible:
            self.bottom_dock.hide()

    def _refresh_project_tree(self) -> None:
        self.project_tree.clear()
        self.region_table.setRowCount(0)
        if self.project is None:
            self.canvas.clear_regions()
            return
        self.canvas.set_regions(self.project.regions, self.project.topology)
        if self.canvas.geometry_errors:
            details = "\n".join(
                f"{self.project.regions[region_id].name}: {message}"
                for region_id, message in self.canvas.geometry_errors.items()
                if region_id in self.project.regions
            )
            self.issue_panel.setPlainText(f"区域几何无法显示：\n{details}")
        else:
            self.issue_panel.setPlainText("当前没有校验问题。")
        root = QTreeWidgetItem([self.project.name, "工程"])
        root.setData(0, Qt.ItemDataRole.UserRole, self.project.id)
        self.project_tree.addTopLevelItem(root)
        regions_by_level = {level.id: [] for level in self.project.hierarchy_levels}
        for region in self.project.regions.values():
            regions_by_level.setdefault(region.level_id, []).append(region)
        region_items: dict[str, QTreeWidgetItem] = {}
        for level in self.project.hierarchy_levels:
            level_item = QTreeWidgetItem([level.name, level.default_type_name])
            level_item.setData(0, Qt.ItemDataRole.UserRole, level.id)
            root.addChild(level_item)
            for region in regions_by_level.get(level.id, []):
                child = QTreeWidgetItem([region.name, region.type_name])
                child.setData(0, Qt.ItemDataRole.UserRole, region.id)
                if region.parent_id in region_items:
                    region_items[region.parent_id].addChild(child)
                else:
                    level_item.addChild(child)
                region_items[region.id] = child
                row = self.region_table.rowCount()
                self.region_table.insertRow(row)
                self.region_table.setItem(row, 0, QTableWidgetItem(region.name))
                self.region_table.setItem(row, 1, QTableWidgetItem(level.name))
                self.region_table.setItem(row, 2, QTableWidgetItem(str(region.status)))
        root.setExpanded(True)
        self.project_tree.expandAll()
        author = self.project.metadata.get("author", "未填写")
        self.property_label.setText(
            f"工程：{self.project.name}\n作者：{author}\n"
            f"说明：{self.project.description or '未填写'}\n"
            f"世界：{self.project.world_width_km:g} × {self.project.world_height_km:g} km\n"
            f"层级：{len(self.project.hierarchy_levels)}"
        )

    def _project_tree_changed(self, current, _previous) -> None:
        if self.project is None:
            return
        if current is None:
            self.canvas.clear_selection()
            return
        value = current.data(0, Qt.ItemDataRole.UserRole)
        tree_region_ids = [
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in self.project_tree.selectedItems()
            if item.data(0, Qt.ItemDataRole.UserRole) in self.project.regions
        ]
        if len(tree_region_ids) > 1:
            self.canvas.set_selection(tree_region_ids)
            self.property_label.setText(f"已选择 {len(tree_region_ids)} 个区域")
            self.statusBar().showMessage(f"已选择 {len(tree_region_ids)} 个区域", 3000)
            return
        for region in self.project.regions.values():
            if region.id == value:
                self.canvas.set_selection([region.id])
                self.property_label.setText(
                    f"区域：{region.name}\n类型：{region.type_name}\n状态：{region.status}"
                )
                self.statusBar().showMessage(f"已定位：{region.name}", 3000)
                return
        region_ids = [
            region.id for region in self.project.regions.values() if region.level_id == value
        ]
        if region_ids:
            self.canvas.set_selection(region_ids)
        else:
            self.canvas.clear_selection()
        self.statusBar().showMessage(f"当前层级：{current.text(0)}", 3000)

    def _canvas_selection_changed(self, region_ids) -> None:
        selected = tuple(region_ids)
        count = len(selected)
        area = self.canvas.selected_area_km2
        self.selection_status.setText(
            "未选择" if not count else f"已选择 {count} 个区域 · {area:g} km²"
        )
        if self.project is None:
            return
        self.project_tree.blockSignals(True)
        try:
            for item in self._tree_items():
                value = item.data(0, Qt.ItemDataRole.UserRole)
                item.setSelected(value in selected)
            if len(selected) == 1 and selected[0] in self.project.regions:
                region = self.project.regions[selected[0]]
                self.property_label.setText(
                    f"区域：{region.name}\n类型：{region.type_name}\n状态：{region.status}\n"
                    f"相邻区域：{len(region_adjacency(self.project, region.id))}"
                )
            elif count > 1:
                self.property_label.setText(f"已选择 {count} 个区域\n面积：{area:g} km²")
            else:
                self.property_label.setText(f"工程：{self.project.name}")
        finally:
            self.project_tree.blockSignals(False)

    def _tree_items(self):
        def walk(item):
            yield item
            for index in range(item.childCount()):
                yield from walk(item.child(index))

        for index in range(self.project_tree.topLevelItemCount()):
            yield from walk(self.project_tree.topLevelItem(index))

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
        edit_menu = self.menuBar().addMenu("编辑(&E)")
        self.undo_action = self.undo_stack.createUndoAction(self, "撤销")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.undo_stack.createRedoAction(self, "重做")
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        self.merge_action = QAction("合并相邻区域", self)
        self.merge_action.setShortcut(QKeySequence("Ctrl+M"))
        self.merge_action.triggered.connect(self._merge_selected)
        edit_menu.addAction(self.merge_action)
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
        self.draw_action = QAction("绘制区域", self)
        self.draw_action.setCheckable(True)
        self.draw_action.setShortcut(QKeySequence("D"))
        self.draw_action.toggled.connect(self._set_draw_mode)
        view_menu.addAction(self.draw_action)
        self.freehand_action = QAction("自由手绘", self)
        self.freehand_action.setCheckable(True)
        self.freehand_action.setShortcut(QKeySequence("F"))
        self.freehand_action.toggled.connect(self._set_freehand_mode)
        view_menu.addAction(self.freehand_action)
        self.component_action = QAction("添加区域部件", self)
        self.component_action.setCheckable(True)
        self.component_action.setShortcut(QKeySequence("A"))
        self.component_action.toggled.connect(self._set_component_mode)
        view_menu.addAction(self.component_action)
        self.split_action = QAction("切分区域", self)
        self.split_action.setCheckable(True)
        self.split_action.setShortcut(QKeySequence("S"))
        self.split_action.toggled.connect(self._set_split_mode)
        view_menu.addAction(self.split_action)
        self._create_panel_actions(view_menu)

        self.navigation_toolbar = QToolBar("地图导航", self)
        self.navigation_toolbar.setObjectName("navigationToolbar")
        self.navigation_toolbar.addAction(self.pan_action)
        self.navigation_toolbar.addAction(self.draw_action)
        self.navigation_toolbar.addAction(self.freehand_action)
        self.navigation_toolbar.addAction(self.component_action)
        self.navigation_toolbar.addAction(self.split_action)
        self.navigation_toolbar.addSeparator()
        self.navigation_toolbar.addAction(self.undo_action)
        self.navigation_toolbar.addAction(self.redo_action)
        self.navigation_toolbar.addSeparator()
        self.navigation_toolbar.addAction(self.zoom_out_action)
        self.navigation_toolbar.addAction(self.zoom_in_action)
        self.navigation_toolbar.addAction(self.fit_world_action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.navigation_toolbar)
        self.navigation_toolbar.hide()
        self._set_workspace_docks_visible(False)

    def _create_navigation_status(self) -> None:
        self.coordinate_status = QLabel("X —  Y —")
        self.coordinate_status.setObjectName("worldCoordinateStatus")
        self.zoom_status = QLabel("缩放 —")
        self.zoom_status.setObjectName("zoomStatus")
        self.selection_status = QLabel("未选择")
        self.selection_status.setObjectName("selectionStatus")
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setObjectName("zoomSlider")
        self.zoom_slider.setRange(int(MapCanvas.MIN_ZOOM_PERCENT), int(MapCanvas.MAX_ZOOM_PERCENT))
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(140)
        self.zoom_slider.valueChanged.connect(self.canvas.set_zoom_percent)
        self.statusBar().addPermanentWidget(self.coordinate_status)
        self.statusBar().addPermanentWidget(self.zoom_status)
        self.statusBar().addPermanentWidget(self.selection_status)
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
        self.draw_action.setChecked(False)
        self.freehand_action.setChecked(False)
        self.split_action.setChecked(False)
        self.canvas.clear_regions()
        self.pan_action.setChecked(False)
        self.coordinate_status.setText("X —  Y —")
        self.zoom_status.setText("缩放 —")
        self.selection_status.setText("未选择")
        self.statusBar().showMessage("就绪")
        self._update_actions()

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        if self._confirm_discard():
            self._load_project(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _history_index_changed(self, _index: int) -> None:
        self.dirty = not self.undo_stack.isClean()
        self._update_actions()

    def _apply_history_snapshot(self, project: Project, selected_ids, message: str) -> None:
        self.project = project
        self._refresh_project_tree()
        self.canvas.set_selection(selected_ids)
        self.operation_log.appendPlainText(message)
        self.statusBar().showMessage(message, 4000)
        self.dirty = not self.undo_stack.isClean()
        self._update_actions()

    def _reset_history(self) -> None:
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self.dirty = False

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
        self.draw_action.setEnabled(writable)
        self.freehand_action.setEnabled(writable)
        self.component_action.setEnabled(writable and len(self.canvas.selected_region_ids) == 1)
        self.split_action.setEnabled(writable and bool(self.canvas.selected_region_ids))
        self.merge_action.setEnabled(writable and len(self.canvas.selected_region_ids) == 2)

    def _set_draw_mode(self, enabled: bool) -> None:
        if enabled and (self.project is None or self.read_only):
            self.draw_action.setChecked(False)
            return
        if enabled:
            self.pan_action.setChecked(False)
            self.freehand_action.setChecked(False)
            self.split_action.setChecked(False)
            self.statusBar().showMessage(
                "绘制区域：单击添加顶点，点击首点闭合；右键/Backspace 删除最后一点，Esc 取消"
            )
        else:
            self.canvas.cancel_drawing()
        self.canvas.set_draw_mode(enabled)

    def _set_freehand_mode(self, enabled: bool) -> None:
        if enabled and (self.project is None or self.read_only):
            self.freehand_action.setChecked(False)
            return
        if enabled:
            self.pan_action.setChecked(False)
            self.draw_action.setChecked(False)
            self.component_action.setChecked(False)
            self.split_action.setChecked(False)
            self.statusBar().showMessage("自由手绘：按住鼠标左键拖动勾勒区域，松开完成；Esc 取消")
        else:
            self.canvas.cancel_freehand()
        self.canvas.set_freehand_mode(enabled)

    def _set_component_mode(self, enabled: bool) -> None:
        if enabled and (
            self.project is None or self.read_only or len(self.canvas.selected_region_ids) != 1
        ):
            self.component_action.setChecked(False)
            self.statusBar().showMessage("请先选择一个可写的区域。", 4000)
            return
        if enabled:
            self.pan_action.setChecked(False)
            self.draw_action.setChecked(False)
            self.freehand_action.setChecked(False)
            self.split_action.setChecked(False)
            self.statusBar().showMessage(
                "添加区域部件：按住鼠标左键绘制岛屿或飞地，松开确认；Esc 取消"
            )
        else:
            self.canvas.cancel_freehand()
        self.canvas.set_freehand_mode(enabled)

    def _set_split_mode(self, enabled: bool) -> None:
        if enabled and (
            self.project is None or self.read_only or len(self.canvas.selected_region_ids) != 1
        ):
            self.split_action.setChecked(False)
            self.statusBar().showMessage("请先选择一个可写的单面父区域。", 4000)
            return
        if enabled:
            self.pan_action.setChecked(False)
            self.draw_action.setChecked(False)
            self.freehand_action.setChecked(False)
            self.statusBar().showMessage("切分区域：按住左键拖过父区域，松开预览切分；Esc 取消")
        else:
            self.canvas.cancel_split()
        self.canvas.set_split_mode(enabled)

    def _split_completed(self, points) -> None:
        if self.project is None or self.read_only:
            return
        selected = tuple(self.canvas.selected_region_ids)
        if len(selected) != 1:
            self.issue_panel.setPlainText("切分区域失败：请先选择一个父区域。")
            return
        parent = self.project.regions[selected[0]]
        try:
            preview = preview_region_split(self.project, selected[0], points)
        except RegionCommandError as exc:
            self.issue_panel.setPlainText(f"切分预览失败：\n{exc}")
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.canvas.set_split_preview(preview.pieces)
        dialog = RegionSplitDialog(
            parent.name,
            self,
            piece_count=len(preview.pieces),
            minimum_area=preview.minimum_area,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._commit_split(
            selected[0],
            points,
            dialog.names,
            dialog.type_name,
        )

    def _commit_split(self, region_id, points, names, type_name: str) -> None:
        if self.project is None:
            return
        before_project = self.project
        before_selection = self.canvas.selected_region_ids
        try:
            after_project, children = split_region_into_children(
                self.project, region_id, points, names, type_name
            )
        except RegionCommandError as exc:
            self.issue_panel.setPlainText(f"切分区域失败：\n{exc}")
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.undo_stack.push(
            ProjectSnapshotCommand(
                self,
                before_project,
                after_project,
                before_selection,
                tuple(child.id for child in children),
                f"切分区域：{before_project.regions[region_id].name}",
            )
        )
        self.canvas.cancel_split()
        self.split_action.setChecked(False)
        self.statusBar().showMessage("已切分区域（可撤销）", 5000)

    def _merge_selected(self) -> None:
        if self.project is None or self.read_only:
            return
        selected = tuple(self.canvas.selected_region_ids)
        if len(selected) != 2:
            self.statusBar().showMessage("请先选择两个相邻的同级区域。", 4000)
            return
        try:
            merged_project, merged = merge_sibling_regions(self.project, selected)
        except RegionCommandError as exc:
            self.issue_panel.setPlainText(f"合并区域失败：\n{exc}")
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.undo_stack.push(
            ProjectSnapshotCommand(
                self,
                self.project,
                merged_project,
                selected,
                (merged.id,),
                f"合并区域：{merged.name}",
            )
        )
        self.statusBar().showMessage("已合并相邻区域（可撤销）", 5000)

    def _polygon_completed(self, points) -> None:
        if self.project is None or self.read_only:
            return
        polygon, error = self._validate_draft_polygon(points)
        if error:
            self.issue_panel.setPlainText(f"绘制区域无法创建：\n{error}")
            self.statusBar().showMessage(error, 5000)
            return
        dialog = RegionCreateDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._commit_region(polygon, dialog.region_name, dialog.type_name)

    def _freehand_completed(self, points) -> None:
        if self.project is None or self.read_only:
            return
        try:
            polygon = polygon_from_freehand(
                points,
                tolerance=max(self.project.tolerances.minimum_face_area**0.5 * 0.01, 1e-6),
            )
        except RegionCommandError as exc:
            self.issue_panel.setPlainText(f"自由手绘无法创建：\n{exc}")
            self.statusBar().showMessage(str(exc), 5000)
            return
        polygon, error = self._validate_draft_polygon(tuple(polygon.exterior.coords[:-1]))
        if error:
            self.issue_panel.setPlainText(f"自由手绘无法创建：\n{error}")
            self.statusBar().showMessage(error, 5000)
            return
        if self.component_action.isChecked() and len(self.canvas.selected_region_ids) == 1:
            try:
                after_project, updated = add_region_component(
                    self.project, tuple(self.canvas.selected_region_ids)[0], polygon
                )
            except RegionCommandError as exc:
                self.issue_panel.setPlainText(f"添加区域部件失败：\n{exc}")
                self.statusBar().showMessage(str(exc), 5000)
                return
            before_selection = self.canvas.selected_region_ids
            self.undo_stack.push(
                ProjectSnapshotCommand(
                    self,
                    self.project,
                    after_project,
                    before_selection,
                    (updated.id,),
                    f"添加区域部件：{updated.name}",
                )
            )
            self.canvas.cancel_freehand()
            self.component_action.setChecked(False)
            self.statusBar().showMessage("已添加区域部件（可撤销）", 5000)
            return
        dialog = RegionCreateDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._commit_region(polygon, dialog.region_name, dialog.type_name)

    def _validate_draft_polygon(self, points):
        if len(points) < 3:
            return None, "区域至少需要三个顶点。"
        coords = [
            (point.x(), point.y()) if hasattr(point, "x") else (float(point[0]), float(point[1]))
            for point in points
        ]
        polygon = Polygon(coords)
        if not polygon.is_valid:
            return None, f"区域几何无效：{explain_validity(polygon)}"
        tolerance = self.project.tolerances
        if polygon.area <= tolerance.minimum_face_area:
            return None, "区域面积为零或小于工程容差。"
        min_x, min_y, max_x, max_y = polygon.bounds
        if (
            min_x < 0
            or min_y < 0
            or max_x > self.project.world_width_km
            or max_y > self.project.world_height_km
        ):
            return None, "区域不能超出世界边界。"
        for region in self.project.regions.values():
            if not region.visible:
                continue
            for face_id in region.face_ids:
                try:
                    existing = self.project.topology.face_polygon(face_id)
                except (KeyError, ValueError, GEOSException):
                    continue
                if existing.intersection(polygon).area > tolerance.minimum_face_area:
                    return None, f"区域与已有区域“{region.name}”重叠。"
        return orient(polygon, sign=1.0), None

    def _commit_region(self, polygon, name: str, type_name: str) -> None:
        if self.project is None:
            return
        before_project = self.project
        before_selection = self.canvas.selected_region_ids
        try:
            after_project, region = create_top_level_region(self.project, polygon, name, type_name)
        except RegionCommandError as exc:
            self.issue_panel.setPlainText(f"区域创建失败：\n{exc}")
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.undo_stack.push(
            ProjectSnapshotCommand(
                self,
                before_project,
                after_project,
                before_selection,
                (region.id,),
                f"创建顶级区域：{name}（{type_name}）",
            )
        )
        self.canvas.cancel_drawing()
        self.canvas.cancel_freehand()
        self.canvas.cancel_split()
        self.draw_action.setChecked(False)
        self.freehand_action.setChecked(False)
        self.split_action.setChecked(False)
        self.statusBar().showMessage(f"已创建区域：{name}（可撤销）", 5000)

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
            self._reset_history()
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
        self._reset_history()
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
        self.undo_stack.setClean()
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
        if not self._has_restored_dock_state:
            self._set_workspace_docks_visible(True)
        self.canvas.set_draw_mode(False)
        self.canvas.set_freehand_mode(False)
        self.canvas.set_split_mode(False)
        self.draw_action.setChecked(False)
        self.freehand_action.setChecked(False)
        self.split_action.setChecked(False)
        self._refresh_project_tree()
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
        if state.window_state and not self.restoreState(QByteArray(state.window_state), 1):
            logger.warning("Invalid dock layout; using default layout")
        elif state.window_state:
            self._has_restored_dock_state = True

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
            self.settings.save(
                WindowSettings(
                    geometry=bytes(self.saveGeometry()),
                    window_state=bytes(self.saveState(1)),
                )
            )
        except SettingsError as exc:
            self.settings_error = str(exc)
            if self.interactive:
                QMessageBox.warning(self, "设置未保存", str(exc))
        logger.info("Window closed")
        super().closeEvent(event)
