# 开发续接记录

## 2026-10-11 晚间：M0 工程骨架

用户已批准本轮 M0 计划。后续轮次仍需单独提交计划并获得批准。

### 已实现

- 原版规范保存于 `docs/spec/`，需求索引记录内容摘要校验值。
- Python 3.12+ / PyQt6 包结构、GUI 入口、集中主题、空主窗口。
- 真实可用的退出、日志目录、关于菜单；窗口几何设置保存/恢复。
- 原子设置写入；损坏和未来版本设置保留；写入失败有反馈。
- 本地 5 MB 轮换日志，总计最多 5 个文件，记录启动、关闭与运行时版本。
- pytest / pytest-qt 测试、锁定依赖、Windows/Linux GitHub Actions。
- wheel / sdist 构建与脱离源码目录的独立安装启动验证。

### 本地验证（Linux / Python 3.12.14 / PyQt6 6.11.0 / Qt 6.11.2）

| 检查 | 结果 |
|---|---|
| `ruff check .` / `ruff format --check .` | 通过 |
| `pytest -q --junitxml=test-results/pytest.xml` | 14 项通过 |
| `uv build --no-build-isolation` | wheel 和 sdist 构建成功 |
| `python scripts/verify_wheel.py` | 独立虚拟环境安装并运行成功 |
| 真实主窗口截图 | 已生成并检查；空主窗口符合 M0 范围 |
| GitHub Actions | Windows / Linux 全部通过，见下方运行链接 |
| Windows 实机人工验收 | 待完成，见 `acceptance/M0.md` |

发现并处理：无头 Qt 默认屏幕为 800×800，导致恢复几何时按屏幕约束缩小。
pytest 使用明确的 1920×1080 虚拟显示配置；Windows 测试使用相对配置路径，避免盘符冒号冲突。
截图环境临时加载 Noto Sans CJK SC；字体不打包进产品，Windows 使用系统中文字体。

### 未完成与下轮建议

M0 实现、本地自动化与双平台 CI 已完成；人工验收待完成，不宣称完整里程碑已验收。
尚无地图领域模型、地图工程 IO、绘制、拓扑编辑、撤销、导入导出或恢复功能。

1. 先检查远端 HEAD 和 CI；修复实际失败后再进入下一里程碑。
2. 提交 M1 首批计划：Project / Hierarchy / Region 的纯数据模型、规则测试与基础拓扑设计。
3. 后续再实现 `.atlasweave` 无损往返、原子保存失败保护、未知字段保留和迁移框架。
4. 用户每周验收时，按 M0 清单验证 Windows 桌面行为。

禁止依赖 scratch 仍存在。重新开始时从 GitHub 拉取源码，用锁文件恢复环境。
定时安排：北京时间每日 06:30、22:00，只提交当轮计划并等待审批。

### 已提交的验证证据

- 实现提交：`22392aa28ad7b85f6dd5a3687c68a468d710e924`。
- [GitHub Actions 第 1 次运行](https://github.com/whyhaoyu1022-glitch/Atlasveave/actions/runs/38151046535)：Windows 和 Linux 两个作业均 success。
- 两个平台均通过静态检查、自动化测试、真实窗口冒烟、wheel/sdist 构建及脱离源码的独立安装启动。
- 运行附件 `m0-windows-latest` / `m0-ubuntu-latest` 包含测试报告、窗口截图和 Python 分发包；不是正式 Windows EXE 安装包。
- [本地实际窗口截图](acceptance/m0-window.png)。
- 本次收尾仅更新文档，沿用上述实现提交的测试证据。

## 2026-10-12 早间：M1 第一批模型与基础拓扑

用户已明确批准本轮计划。实现纯数据模型、关系校验和共享边基础，并沿用 M0 回归。

### 本轮已实现

- Project / HierarchyLevel / Region：字段、UUID v4、有限坐标、非负统计值与状态规则。
- frozen 快照与只读映射，未知扩展数据在内存中保留，不依赖 Qt。
- Vertex / Edge / HalfEdge / Ring / Face 与方向求值；一个共享边只有一份坐标数据。
- 拓扑候选变换保留 UUID；共享边两侧和三区域结点同步变化，原快照不改。
- 层级顺序、父子引用和循环、草稿规则、悬空引用、环闭合/方向/自交、飞地与洞校验。
- 同父同层重叠、父级越界、独立重复公共边界和非流形边报错；空间索引用于筛选相邻候选。
- 增加锁定的 Shapely 2.2.0 / NumPy 2.5.4 依赖；独立安装包域校验测试。

### 本地验证

| 检查 | 结果 |
|---|---|
| ruff / 格式检查 | 通过 |
| 完整 pytest 回归 | 99 项通过（原有 14 项保留，新增 85 项） |
| 共享边 16 个固定 seed 候选 | 双方同步、无面积重叠、总面积守恒 |
| wheel / sdist 构建 | 成功 |
| 独立环境安装包 | 域导入无 Qt、模型校验、主窗口启动/关闭通过 |
| GitHub Windows / Linux CI | 两个平台全部通过，见下方本轮运行证据 |
| Windows 实机人工验收 | 仍待用户执行 M0 清单 |

### 本轮发现并处理

- 拓扑映射键与内部 ID 不一致时，必须返回校验问题，不能在校验期间崩溃。
- 父子区域跨层重用外边界不能误判成同级半边方向冲突。
- 曲线尚未求值时必须明确报未支持，不能静默变成直线。
- 越出世界范围的面不参与后续空间运算，保留准确问题记录。

### 续接与未完成

本轮只完成 M1 第一批内存模型；完整 M1 尚缺 `.atlasweave` 读写、原子工程保存、迁移与最近工程。
界面仍为 M0 骨架；UI 命令、撤销、锁定编辑约束、曲线编辑、自定义字段完整规则后续接入。
下轮先核对本次远端提交与 CI，优先处理失败；建议提交 M1 第二批工程 IO 与无损往返计划。
读取 `docs/DOMAIN_MODEL.md`，按只读快照显式序列化，避免 `dataclasses.asdict` 对只读映射的深拷贝问题。
每周验收材料新增 `docs/acceptance/M1-01.md`；本轮批准不自动授权下轮。

### 本轮提交与 CI 证据

- 实现提交：`1bcefc5581bd22152df62851517c694768e142b7`。
- [本轮 GitHub Actions](https://github.com/whyhaoyu1022-glitch/Atlasveave/actions/runs/38181714497)：Windows / Linux 作业均 success。
- 静态检查、99 项测试、主窗口冒烟、构建、独立安装后领域模型与 GUI 验证均通过。
- 附件 `atlasweave-windows-latest` / `atlasweave-ubuntu-latest` 包含测试报告、实际骨架窗口截图与 Python wheel/sdist；不是正式 Windows EXE。
- 收尾提交仅补充验收文档，产品代码与上述已验证实现提交一致。

## 2026-10-12 晚间：M1 第二批工程文件 IO

用户已明确批准本轮计划。实现 `.atlasweave` ZIP、无损 codec、原子保存、高版本保护和最近工程服务。

### 本轮已实现

- `project.json` / `topology.json` / `styles.json` / `thumbnail.png` 容器成员与附件安全路径。
- 显式模型 codec：时间、枚举、坐标、只读 JSON、未知顶层/工程/拓扑/区域字段可往返保留。
- Qt QImage/QPainter 真实世界坐标缩略图，保存前复读 ZIP、CRC 和必要成员。
- 同目录随机临时文件与 `os.replace` 原子替换；失败清理临时文件并保留原目标。
- `.bak1`–`.bakN` 复制轮换，备份失败告警不阻断主保存；默认 3，范围 0–10。
- 语义版本比较、单向 MigrationRegistry；高版本只读打开、警告、禁止覆盖原路径。
- RecentProjects：带时区时间、置顶、上限、原子写入、移除和损坏恢复。

### 本地验证

| 检查 | 结果 |
|---|---|
| ruff / 格式检查 | 通过 |
| 完整 pytest 回归 | 108 项通过（M0 + M1 第一批 + 本批） |
| `.atlasweave` 样例往返 | 工程、共享拓扑、属性、扩展和附件保留 |
| 原子失败场景 | 原文件保留、临时文件清理 |
| wheel / sdist 与独立环境 | 待本轮 CI 核对 |
| GitHub Windows / Linux CI | 待提交后核对 |
| Windows 实机人工验收 | 仍待用户执行 M0 清单 |

### 续接与未完成

M1 服务层 IO 已完成；尚缺 UI 打开/保存菜单、未保存标记、创建向导、自动保存/崩溃恢复和最近工程欢迎页接入。
下一轮建议先实现 M2 欢迎页/新建向导/主窗口导航，将 ProjectStore 接入真实用户流程，再进入绘制命令。
本轮批准不自动授权下轮；中断后从远端提交、CI 和本文件恢复。


### 本批收尾证据

- 实现提交：`046165595ac8c9c9315e9d31b52c876ea87371bb`。
- [GitHub Actions 运行 38269970209](https://github.com/whyhaoyu1022-glitch/Atlasveave/actions/runs/38269970209)：Windows / Linux 均 success。
- 本地完整回归：109 项测试通过；ruff check、构建、独立 wheel 安装与工程 IO 探针通过。
- 未完成：主窗口尚未接入打开/保存/最近工程菜单；Windows 实机人工验收仍需用户执行。
