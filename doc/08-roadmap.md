# 08 · 路线图

> 状态：草案 · 按版本切片所有要做的事，含里程碑和风险

## 总体节奏

| 版本 | 周期 | 主题 | 关键交付物 |
|------|------|------|-----------|
| **V0.1** | 4-6 周 | 观测层 | spike 产品化，3 个 adapter，本地 web UI |
| **V0.2** | 4-6 周 | 编排层 | task / agent 注册 / budget / Controller |
| **V0.3** | 6-8 周 | Workflow | DAG 可视化 / 自动推导 / 触发器 |
| **V0.4** | 4-6 周 | HITL | approval gate / 远程审批 / pattern 规则 |
| **V0.5+** | 持续 | 长尾 | artifact 索引 / 多机器 / Pixie 集成 |

总长度约 5-6 个月达到稳定 V0.4。

---

## V0.1 详细任务（观测层）

### 里程碑 M1.1 · daemon 骨架（1 周）

- [x] 项目脚手架（uv init）
- [x] FastAPI app + healthcheck
- [x] SQLite + 迁移 runner
- [x] 基础日志（structlog）
- [x] CLI 入口（`agent-master start`）
- [x] 配置文件加载（toml）

**验收：** `agent-master start` 起来，访问 `/api/health` 返回 ok。 ✅ 完成（commit `dbba843..22c91be`）

### 里程碑 M1.2 · 数据层 + adapter 抽象（1 周）

- [x] 数据模型 ORM-less 实现（dataclass + 手写 SQL）
- [x] 7 个核心对象 schema 上线
- [x] AdapterRegistry / Observer 基类
- [x] 跨平台 watcher 抽象（fsevents/inotify/polling）

**验收：** 单元测试覆盖 7 个对象的 CRUD。 ✅ 48 tests / 81% coverage（commit `02d62ad..e70ba3c`）

### 里程碑 M1.3 · 三个 Adapter（2 周）

- [x] OpenCode adapter（schema 最干净，作为 reference 实现 — session/message/part/event + parent_id 全直给）
- [x] Hermes adapter（spike 已验证 WAL tail 路径，复用代码）
- [x] Claude Code adapter
- [x] 每个 adapter 的 fixture + 单元测试

**验收：** daemon 起来，看到 ~/.local/share/opencode ~/.claude ~/.hermes 的 session 都被发现。 ✅ 完成（commit `1fffead..0d38a6f`，65 tests 全绿，3 个 adapter 都用真实 DB smoke 验证过）

### 里程碑 M1.4 · 事件流 + SSE（1 周）

- [x] Event Pipeline（防抖、持久化、fanout）
- [x] SSE endpoint
- [x] DebouncedFanout 实现

**验收：** curl SSE 端点，触发一个 claude code 操作，看到事件实时流出。 ✅ 完成（commit `fb2b7ee..` ，scripts/smoke_real_daemon.py 端到端验证通过）

### 里程碑 M1.5 · UI MVP（2 周）

- [ ] Vite 项目脚手架
- [ ] 视图 A（全景看板）
- [ ] 视图 B（session 详情，简化版：纯事件流）
- [ ] 视图 C（拓扑图，React Flow 基础）
- [ ] SSE hook
- [ ] Dark mode

**验收：** 用户装上就能看到 5 个 agent 实时活动。

### V0.1 风险

| 风险 | 应对 |
|------|------|
| Claude Code 日志格式变化 | adapter 写 schema 检测，警告但不挂 |
| 多 session 并发 SSE 性能 | benchmark + 必要时改 WebSocket |
| 跨平台兼容（Linux 用户少但有） | fsevents/inotify 抽象层 + CI 测两个平台 |
| 用户 ~/.claude 路径不标准 | 配置可改路径 + 自动检测多个候选 |

### V0.1 不做（关键边界）

- ❌ 任何写入 agent 数据
- ❌ 编排 / 派活
- ❌ 审批
- ❌ workflow
- ❌ 远程访问

---

## V0.2 详细任务（编排层）

### 里程碑 M2.1 · Task / Agent 模型 + UI（1.5 周）

- [ ] Task 表 + CRUD API
- [ ] Agent 注册 UI（不再只是被动发现）
- [ ] Budget 表 + 计算
- [ ] task 列表 / 详情 / 创建 UI

### 里程碑 M2.2 · Controller 协议（1.5 周）

- [ ] Controller 基类
- [ ] OpenCode Controller（spawn 模式）
- [ ] Claude Code Controller（spawn 模式）
- [ ] Hermes Controller（inject 模式）
- [ ] 进程生命周期管理（pause/resume/cancel）

### 里程碑 M2.3 · Budget 强制（1 周）

- [ ] cost 累计
- [ ] 超限暂停 agent
- [ ] UI 显示预算

### 里程碑 M2.4 · Audit Log + UI（1 周）

- [ ] activity_log 表
- [ ] 所有 mutation 写 log
- [ ] activity 视图

### V0.2 风险

| 风险 | 应对 |
|------|------|
| Spawn 进程的环境变量、PATH | 借鉴 paperclip controller 实现 |
| 进程崩溃后状态不一致 | 启动时扫"isolated processes" + 标记 |
| Budget 准确性（token 计算口径） | 参考各家 official tokenizer |

---

## V0.3 详细任务（Workflow）

### 里程碑 M3.1 · DAG 数据 + 自动推导（2 周）

- [ ] Workflow / WorkflowRun / WorkflowNodeRun 表
- [ ] 从历史 sessions 推导 DAG 算法
- [ ] "save as workflow" UI

### 里程碑 M3.2 · YAML DSL 解析 + 运行 (2 周）

- [ ] YAML parser + validator
- [ ] WorkflowExecutor（拓扑排序 + 依赖检查）
- [ ] 节点输出传递机制

### 里程碑 M3.3 · 触发器（2 周）

- [ ] Cron 触发器
- [ ] 文件变化触发器
- [ ] Webhook 触发器（HTTP 端点）

### 里程碑 M3.4 · Workflow UI（2 周）

- [ ] Workflow 列表
- [ ] DAG 可视化（React Flow 增强）
- [ ] 运行历史 / 回放
- [ ] 编辑 DSL（codemirror）

### V0.3 风险

| 风险 | 应对 |
|------|------|
| DSL 表达力不够（条件、循环） | 先 happy path，复杂逻辑 V0.4 加 |
| 自动推导 prompt template 不准 | UI 标记 "draft"，用户必须 review |
| 长 workflow 状态管理 | 借鉴 temporal 的 history replay 思想 |

---

## V0.4 详细任务（HITL）

### 里程碑 M4.1 · Approval 数据 + Hook 路径（2 周）

- [ ] Approval / Rule 表（V0.1 已上线 schema）
- [ ] `/api/approve` 端点
- [ ] Claude Code PreToolUse hook 脚本
- [ ] OpenCode hook 脚本（如其支持；否则走 V0.4 proxy 路径备份）

### 里程碑 M4.2 · 规则引擎（1.5 周）

- [ ] Rule 匹配引擎
- [ ] 规则预设
- [ ] Rule 管理 UI

### 里程碑 M4.3 · 桌面 UI（1 周）

- [ ] Pending approvals 列表
- [ ] 决策对话框（含 diff、批量授信选项）
- [ ] 系统通知

### 里程碑 M4.4 · 远程通道（1.5 周）

- [ ] ntfy push 集成
- [ ] Tailscale 文档
- [ ] 手机端审批页（响应式）
- [ ] Bearer token 认证

### V0.4 风险

| 风险 | 应对 |
|------|------|
| Hook 阻塞太久 agent 超时 | 配置 hook timeout = approval timeout |
| 规则冲突 | 显式优先级：deny > ask > allow |
| 手机推送被忽略 | 重要操作多通道（push + email + 桌面） |
| 网络不通审批不到 | timeout 后 default_action 兜底 |

---

## V0.5+ 长尾（不承诺时间）

按优先级：

1. **Artifact 索引** — 让 agent 输出可搜索、可引用
2. **Pixie 集成** — electron-sprite 直接吃我们的事件流做事后统计
3. **跨 session context 复用** — A 干完的成果 B 可读
4. **死循环 / 卡死检测** — 主动 alert
5. **行为模式分析** — 哪类 session 高产出
6. **多机器同步** — 笔记本 + 台式机
7. **Codex adapter** — spike 实测 L2 实时最好，演示价值高
8. **omp adapter** — 半套观测（只有 prompt 历史，无 agent 输出）
9. **CrewAI / LangGraph adapter** — 框架级 agent
10. **Skills / playbook 沉淀** — 把经验固化

---

## 不做（明确边界）

| 不做 | 原因 |
|------|------|
| 多租户 SaaS | local-first，paperclip 在做了 |
| 自己的 agent runtime | 已经 5 个了 |
| Prompt 编辑器 | Cursor / Continue 在做 |
| 通用 chatbot 前端 | OpenWebUI 在做 |
| Jira / Linear 替代 | paperclip 在做 |
| 企业 RBAC | local-first，YAGNI |

---

## 成功标准

### V0.1 成功
- 你自己每天用它看 5 个 agent
- 不会因为它崩溃丢数据
- 性能在预算内（空闲 <1% CPU）

### V0.2 成功
- 你能从 UI 派活给 agent，不再开 5 个终端
- 一周不爆预算

### V0.3 成功
- 你定义过 3 个以上 workflow 并复用
- "自动推导" 至少推出 1 个有用的模板

### V0.4 成功
- 你出门 4 小时，回家不发现 agent 删了不该删的
- 手机审批用得顺手

### 整体成功
- **如果你 6 个月后还在每天用，就成了。**
- 如果别人也开始用，是 bonus。
- 如果有 1k+ star 是惊喜，但不是目标。

---

## 团队 / 工作模式假设

- **执行者：** 主要是你 + AI agent（用 agent-master 自己干自己）
- **节奏：** 弹性，孩子优先，二宝出生窗口（V0.1 期间）有停摆
- **质量门：** 不追求完美，但每个里程碑必须可用 + 有测试

---

## 开发的 dogfooding 策略

**用 agent-master 开发 agent-master。** 关键节点：

1. V0.1 一出来，把它指向自己的开发 session（meta）
2. V0.2 一出来，开始用 task 模型管自己的 dev tasks
3. V0.3 一出来，定义 "feature dev" workflow 自己跑
4. V0.4 一出来，把 git push / npm publish 加 approval gate

吃自己的狗粮是发现真实问题的最快路径。

---

## 关联资源

- spike: `~/sideproject/agent-master/witness.py`
- 参考代码库: `~/paperclip/` (MIT)
- 关联项目: `~/sideproject/electron-sprite/` (Pixie，事后统计层)
- 调研笔记: 见 00-vision.md / 05-hitl.md 中的对比表
