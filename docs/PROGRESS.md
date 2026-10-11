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
