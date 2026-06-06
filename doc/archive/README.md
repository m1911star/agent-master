# archive/

这里放**早期被取代但内容仍有参考价值**的文档。不在主路线上，但可能在实现时
拉回来吸收细节。

## 现有归档

### `02-data-model-typescript-draft.md`

来自更早一版的数据模型设计，**完整的 TypeScript interface 形式**（1072 行）。

被取代的原因：
- 我们决定 backend 用 Python，前端镜像类型靠 OpenAPI 自动生成
- 主线文档（`../01-data-model.md`）用更精简的表格形式表达

仍有价值的部分：
- 详细的 `ApprovalRequest` / `ApprovalPolicy` 字段（可吸收进 05-hitl.md）
- "event-sourced core" 的论述（可吸收进 06-architecture.md）
- 软删除、迁移策略细节

实现 HITL（V0.4）和 architecture 细化时，回来扫一遍。

---

### `workflow-visualization-design.md`

Workflow viz 的**深度技术设计**（611 行）。

被取代的原因：
- 主线文档（`../04-topology.md`）目前是产品级抽象，不深入到 layout 算法
- 主线选了 React Flow，这份是 Cytoscape.js 视角

仍有价值的部分：
- **Dagre vs ELK 选型对比表**（数据非常具体）
- **增量布局算法**（`IncrementalLayoutManager`）
- Edge routing 细节
- 大图（100+ 节点）性能策略

实现 V0.3 workflow viz 时，**必读**。如果实施中发现 React Flow 性能不够，这里
有 Cytoscape 路线的完整方案备用。

---

## 处置规则

- ✅ 这些文档**只读**，不再更新
- ✅ 如果其中内容被吸收进主线，在主线里加 `> 参考: archive/xxx.md` 标注
- ❌ 不要直接修改 archive 里的东西
- ❌ 不要再写新东西放进 archive（新内容进主线或新 doc）
