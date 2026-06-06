# Agent Master — 设计文档

本目录是 Agent Master 正式版的设计文档。从 `~/sideproject/agent-master/witness.py`
spike 长出来，目标是做一个 local-first 的"多 agent 老板看板"。

## TL;DR

一句话：**给你本地所有 coding agent CLI 一个统一的实时观测层 + 拓扑视图 + 远程审批入口。**

你目前同时在用 5 个 agent CLI（Claude Code、Codex、Hermes、OpenCode、Pi）。
witness.py spike 已经能被动抓取它们的日志流。正式版要在此基础上长出：

1. **实时任务状态** — token 级延迟看到每个 agent 在干什么
2. **拓扑可视化** — session 之间的父子/并发关系、workflow DAG
3. **HITL 审批** — 危险操作停下来，能在手机上批准
4. **可扩展 adapter** — 第三方 agent 接入只需实现一组协议

## 阅读顺序

| #  | 文档 | 状态 | 给谁看 |
|----|------|------|--------|
| 00 | [vision.md](./00-vision.md) | 草案 | 所有人（必读，含决策） |
| 01 | [data-model.md](./01-data-model.md) | 草案 | 想接入新 agent 或扩展功能 |
| 02 | [adapter.md](./02-adapter.md) | 草案 | 写 adapter 的开发者 |
| 03 | [realtime.md](./03-realtime.md) | 草案 | 关心延迟/性能 |
| 04 | [topology.md](./04-topology.md) | 草案 | 关心拓扑图/workflow viz |
| 05 | [hitl.md](./05-hitl.md) | 草案 | 关心审批/安全 |
| 06 | [architecture.md](./06-architecture.md) | 草案 | 整体系统架构 |
| 07 | [ui.md](./07-ui.md) | 草案 | 前端开发 |
| 08 | [roadmap.md](./08-roadmap.md) | 草案 | 项目管理/优先级 |

## 状态约定

- **草案** — 第一版写出来，未经压力测试
- **review** — 等待 review，可能有大改
- **已定** — 实现可以基于此开工
- **已实现** — 代码已经按此设计落地

## 关联

- 上游 spike: `~/sideproject/agent-master/witness.py`
- 重要参考: Paperclip (`~/paperclip`, MIT) — 详见 00-vision.md
- 关联项目: `~/sideproject/electron-sprite` (Pixie) — 事后统计层，本项目是过程观测层
- 归档参考: [archive/](./archive/) — 早期被取代但仍有技术参考价值的文档
