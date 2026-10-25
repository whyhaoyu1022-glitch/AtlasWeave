# 疆理 AtlasWeave

基于 Python 3.12+ / PyQt6 的虚构行政地图桌面编辑器。

**当前阶段：M2 工作区和区域选择已接入，M3 已支持真实绘制与创建撤销/重做，M4 已支持首批真实子区域切分；共享边编辑和导出仍未完成，不是 V1 成品。**
仓库名为 `AtlasWeave`，产品与 Python 包名使用规范中的 `AtlasWeave` / `atlasweave`。

## 安装与启动

推荐使用 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```sh
python -m pip install uv==0.12.23
uv sync --frozen --extra dev
uv run --frozen --extra dev python -m atlasweave
```

普通 pip 安装也可用，但要重现 CI 的精确依赖版本请使用上述 `uv.lock`：

```sh
python -m pip install -e ".[dev]"
python -m atlasweave
```

已实现的菜单：文件 → 新建/打开/保存/另存为/退出；视图 → 画布缩放、适应世界、平移、绘制区域、自由手绘、切分区域；帮助 → 打开日志目录 / 关于疆理。
窗口位置和大小在正常退出时保存。日志仅保存在本机，不上传。
默认数据目录由 Qt 按操作系统用户目录确定；测试可以使用 `--data-dir` 隔离。

## 开发验证

```sh
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev pytest
uv run --frozen --extra dev python -m atlasweave --smoke-test --data-dir local-data --screenshot test-results/m0-window.png
uv build --no-build-isolation
```

无显示服务的 Linux 环境需要先设置 `QT_QPA_PLATFORM=offscreen`；Linux 仍需可用的 Qt 系统动态库。
测试的 subprocess 有超时，启动失败不会一直等待无人处理的对话框。

## 规范、计划与验收

- [原版 V1 规范](docs/spec/JIANGLI_ATLASWEAVE_V1_SPEC.md)：不可擅自删减必须功能。
- [需求索引](docs/REQUIREMENTS.md)：覆盖全部规范章节及 AC-01–AC-15。
- [开发路线](docs/ROADMAP.md)：按原文 M0–M8 推进。
- [续接记录](docs/PROGRESS.md)：开始下一轮前先阅读，再核对 GitHub 实际状态。
- [M0 验收](docs/acceptance/M0.md)：自动化证据与人工验收分开记录。
- [M1 第一批验收](docs/acceptance/M1-01.md)；[领域模型接口](docs/DOMAIN_MODEL.md)。
- [M1 第二批验收](docs/acceptance/M1-02.md)；[工程文件格式与 IO](docs/PROJECT_IO.md)。
- [M2 第一批验收](docs/acceptance/M2-01.md)；[M2 第二批验收](docs/acceptance/M2-02.md)；[M2 第三批验收](docs/acceptance/M2-03.md)；[M2 第四批验收](docs/acceptance/M2-04.md)。
- [M2 第五批验收](docs/acceptance/M2-05.md)：真实区域选择、框选和树画布同步。
- [M3 第一批验收](docs/acceptance/M3-01.md)：多边形绘制与顶级区域创建。
- [M3 第二批验收](docs/acceptance/M3-02.md)：自由手绘与顶级区域创建。
- [M3 第三批验收](docs/acceptance/M3-03.md)：区域创建撤销与重做。
- [M4 第一批验收](docs/acceptance/M4-01.md)：真实子区域切分与共享边。
- [M4 第二批验收](docs/acceptance/M4-02.md)：多段切分预览与真实子区域创建。
- [已知问题](docs/KNOWN_ISSUES.md)；[依赖声明](THIRD_PARTY_NOTICES.md)。

每日北京时间 06:30、22:00 提交开发计划，用户批准该轮计划后执行。
所有跨轮进度以提交、测试结果和以上文档为依据，不能依赖临时环境。

## 结构

`app` 负责启动和设置；`domain` 负责纯数据与规则；`services` 负责用例；
`commands` 负责可撤销变更；`ui` 负责呈现。领域数据与基础校验已实现，用户操作与界面按后续里程碑接入。
Domain 不得依赖 Qt 控件，UI 不得绕过命令系统修改工程数据。

源码许可尚待仓库所有者确定；公开仓库不代表已授予特定开源许可。


