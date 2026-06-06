# archive/early-drafts/

来自更早一版本（同一个 session）的代码/DSL 草稿。**不在主线**。

## 文件清单

### TypeScript 代码草稿（11 个）

| 文件 | 主题 | 行数 |
|------|------|------|
| `governance-approval-system.ts` | HITL 系统的 TS 实现草稿 | ~1100 |
| `workflow-types.ts` | Workflow 类型定义 | ~1600 |
| `workflow-visualization.ts` | viz 实现 | ~700 |
| `workflow-instantiation-discovery.ts` | workflow 实例化 | ~650 |
| `workflow-integration.ts` | 集成代码 | ~1400 |
| `workflow-runtime-augmentation.ts` | 运行时扩展 | ~800 |
| `workflow-templates-triggers.ts` | 触发器实现 | ~950 |

### Workflow DSL YAML 示例（6 个）

| 文件 | 主题 |
|------|------|
| `workflow-dsl.yaml` | DSL 总规范 |
| `workflow-dsl-augmentation.yaml` | DSL 扩展 |
| `workflow-dsl-example-deploy.yaml` | 部署流程示例 |
| `workflow-dsl-example-review.yaml` | 代码审查流程示例 |
| `workflow-templates.yaml` | 模板规范 |
| `workflow-templates-examples.yaml` | 模板示例 |

## 为什么归档

1. **语言不一致** — 主线决定 backend 用 Python，这些是 TS 草稿
2. **过度具体** — 还没确定整体架构就写了具体实现，需要重做
3. **混在 doc/ 里污染目录** — 设计文档目录应该只有 markdown

## 价值

- TS 文件里有不少**字段设计**和**算法**是经过深思熟虑的（特别是 governance 和 workflow types）
- YAML DSL 示例展示了**好的 workflow 该长什么样**

## 使用方式

V0.3-V0.4 实现阶段：
1. **不要直接引用这些文件作为实现规范**
2. 但可以打开看作灵感来源
3. 觉得有用的字段或思路 → 吸收进 Python 实现，并在主线 doc 里加注释说"参考 archive/early-drafts/xxx"

将来某个时间点（V0.5+）这里可以彻底删除。
