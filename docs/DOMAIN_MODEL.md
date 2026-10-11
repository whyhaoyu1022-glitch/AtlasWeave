# 领域模型接口：M1 第一批

对应规范 §3.4–3.5、§4.1–4.7、§47。模型与校验无 Qt 依赖，使用 dataclasses、Shapely 2.x。

## 数据和快照

- `Project`：工程 ID、版本、描述、世界尺寸、带时区时间戳、层级、区域、拓扑、样式、自定义字段与扩展。
- `HierarchyLevel`：连续顺序、稳定 ID、名称、默认类型和样式键。
- `Region`：属性、父级、层级、多个面引用、标签、锁定/可见与草稿/有效/无效状态。
- `Vertex → Edge → HalfEdge → Ring → Face`：共享拓扑。Region 只持有面 ID，不持有第二份边界坐标。
- ID 必须是规范 UUID v4 字符串；世界坐标为有限双精度数，原点在左下。
- dataclasses 为 frozen；映射复制为只读映射，嵌套 JSON 映射/数组变为只读映射/元组。
- `extra_fields`、`extensions` 保留内存中的未知 JSON 数据；本批没有文件序列化，不宣称已通过存盘往返。

使用 `dataclasses.replace` 组装候选状态，引用 ID 保持不变。未来由命令系统提交候选，不能直接从 UI 绕过命令修改数据。

## 输入与全工程校验

构造时的无效标量抛出 `ModelError`，包含字段名与可理解的信息。
跨对象或几何问题通过 `validate_project` 返回 `ValidationReport`，不会自动修复或修改快照。

```python
from atlasweave.domain.project import Project
from atlasweave.domain.validation import validate_project

project = Project(name="苍海联邦", world_width_km=1200, world_height_km=800)
report = validate_project(project)
report.require_valid()  # 无错误则返回；有错误则抛出包含完整 report 的异常。
```

问题具有稳定 `code`、中文说明、对象 ID、字段路径和 severity。警告不使 `is_valid` 为假。
检查包括引用、UUID、层级顺序、父级循环、默认相邻上一层规则、环连接与方向、非法面、同级重叠和父级包含。
高级跨层父级仅在调用方明确传入 `allow_skipped_levels=True` 时允许，仍不得形成循环或指向同层/下层。
空区域允许保持 draft；没有几何的 valid 区域会报错。隐藏和锁定不跳过校验。
完整校验结果不直接决定保存策略；规范 §31.2 允许保存带问题的工程，以便修复，后续 IO 应遵循该规则。

## 共享边与层级

`Edge.samples` 仅存两端顶点之间的中间点。半边反向时，端点和采样点整体反转。
环通过拓扑节点 ID 检查连接，不能因坐标接近而自动闭合。

`with_edge_samples` 和 `with_vertex_position` 返回新的候选 Topology，保留对象 UUID 并增加 `geometry_version`。
多个面实时从同一个 Edge/Vertex 求值，因此共享边与多区域结点同时变化；原快照保留。
目前不存面积/包围盒缓存，每次校验按当前几何求值。

相邻半边方向在“同父同层”表面内检查；不同层级的父子区域可以共享外边界的同方向引用，不能被误判成同级邻面。
`Topology.adjacency(face_ids)` 按调用方指定的同级面集合重建边邻接；仅点接触不算邻接。
同父同层的共同几何必须使用共同 Edge；同一条边不能被三个及以上同级区域占有。

曲线描述字段已预留并保留，但 M1 第一批不求值曲线；存在曲线时明确返回未支持，防止静默丢弃。
所有候选仍需经过锁定检查、命令系统、几何与父子校验后才能成为用户编辑结果。
