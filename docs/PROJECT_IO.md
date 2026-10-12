# 工程文件与 IO：M1 第二批

对应规范 §31.2、§33.1–33.6、§47、AC-01、AC-13、AC-15。服务层没有依赖具体窗口控件；UI 接入留给 M2/M3。

## `.atlasweave` 容器

ZIP 必须包含：

- `project.json`：工程基本信息、层级、区域、属性、扩展和未知顶层字段；
- `topology.json`：Vertex/Edge/HalfEdge/Ring/Face、几何版本和拓扑未知字段；
- `styles.json`：当前地图样式 JSON；
- `thumbnail.png`：使用 Qt `QImage`/`QPainter` 从世界坐标真实绘制的 PNG。

可选附件只能位于 `attachments/` 下。通过 `ProjectStore.load()` 获得的 `LoadedProject.attachments` 会被 `save_loaded()` 原样保留。

## 读写安全

`ProjectStore.save()` 先写同目录随机临时文件，复读 ZIP、CRC 和必要成员，再以复制方式轮换 `.bak1`–`.bakN`，最后使用 `os.replace` 原子替换目标。任何步骤失败都会清理临时文件并抛出 `ProjectIOError`；原目标和内存工程不被修改。备份失败只产生 warning，主保存继续；备份数量限制在 0–10，默认 3。

读写不会因为 `ValidationReport` 有错误而拒绝保存，符合 §31.2 的修复工作流。

## 版本、未知字段和迁移

版本使用语义版本比较，当前格式是 `1.0.0`。低版本进入显式注册的单向 `MigrationRegistry`；高于当前版本的工程默认只读打开，带 warning，原路径禁止覆盖，另存为新文件仍可保留数据。已知模型字段使用显式 codec，顶层、工程、拓扑、区域和层级未知字段均作为只读 JSON 原样写回；无法安全表示时明确失败，不静默删除。

## 最近工程

`RecentProjects` 把绝对路径、带时区的最后打开时间和 pinned 状态原子写入 JSON。列表上限为 1–100（默认 20），置顶项优先保留；从列表移除不会删除磁盘工程。损坏记录会写日志并以空列表恢复。

## 已知边界

当前 IO 已可从 Python 服务层验证，但主窗口尚未接入打开/保存菜单、未保存标记、恢复对话框和自动保存。曲线字段保存且明确报告未支持；缩略图不会静默将曲线转换为直线。
