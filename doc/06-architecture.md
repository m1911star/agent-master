# 06 · 系统架构

> 状态：草案 · 整体技术选型 + 进程模型 + 存储 + 部署

## 进程模型（最重要的图）

```
┌────────────────────────────────────────────────────────────┐
│                  agent-master daemon                       │
│                                                            │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────┐   │
│  │  Core API    │    │  Event Bus   │    │  Adapter    │   │
│  │  (FastAPI)   │◄──►│  (in-mem)    │◄──►│  Manager    │   │
│  └──────┬───────┘    └──────┬───────┘    └──────┬──────┘   │
│         │                   │                   │          │
│         ▼                   ▼                   ▼          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              SQLite (WAL mode)                      │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
         ▲              ▲              ▲              ▲
         │              │              │              │
    ┌────┴────┐   ┌─────┴─────┐  ┌─────┴─────┐  ┌─────┴─────┐
    │Adapter: │   │Adapter:   │  │Adapter:   │  │Adapter:   │
    │Claude   │   │Codex      │  │Hermes     │  │<custom>   │
    │(subproc)│   │(subproc)  │  │(subproc)  │  │           │
    └─────────┘   └───────────┘  └───────────┘  └───────────┘

         ┌────────────────────────────────────┐
         │           Web UI (React)           │
         │   SSE/WS ◄────► HTTP API           │
         └────────────────────────────────────┘
```

**关键决策：**
- **单 daemon 进程** 承载 API + Event Bus
- **每个 Adapter 独立子进程**（一个 adapter 崩了不影响别人）
- **SQLite 单文件**（local-first，不依赖外部服务）
- **UI 独立**（React SPA，由 daemon 内置静态文件服务）

---

## 技术栈选型

### Backend

| 维度 | 选型 | 理由 |
|------|------|------|
| 语言 | **Python 3.12+** | spike 用 Python，社区库齐，adapter 写起来快 |
| Web 框架 | **FastAPI** | async 原生、SSE 支持、OpenAPI 自动生成 |
| 任务运行 | **asyncio + 进程池** | 不引入 celery，复杂度不值 |
| 包管理 | **uv** | 用户偏好 |
| DB 层 | **sqlite3 stdlib + 手写迁移** | 不引入 SQLAlchemy，schema 简单不值 |
| 配置 | **TOML** | 比 YAML 简洁，stdlib `tomllib` |

为什么不用 Node？
- spike 是 Python
- adapter 解析 jsonl 用 Python 自然
- 你已有 Python 生态熟练度

为什么不用 Go/Rust？
- V0.1 性能完全够（spike 已验证）
- 上手太慢，路线图压力大

### Frontend

| 维度 | 选型 | 理由 |
|------|------|------|
| 框架 | **React + Vite** | 借鉴 paperclip，生态最大 |
| UI 库 | **shadcn/ui + Tailwind** | 现代、可控、复制粘贴式集成 |
| 状态管理 | **TanStack Query + zustand** | server state + 少量 client state |
| 实时数据 | **SSE 为主，WS 备用** | SSE 简单够用，WS 留给 L3 token |
| 图渲染 | **React Flow** | 04-topology.md 已选型 |
| 路由 | **TanStack Router** | type-safe |
| 包管理 | **pnpm** | 借鉴 paperclip |

详见 07-ui.md。

### 部署

| 维度 | 选型 | 理由 |
|------|------|------|
| 打包 | **pip / uv** | `uv tool install agent-master` |
| 启动 | **`agent-master start`** | 类似 jupyter |
| 后台 | **systemd / launchd** | 用户选装 |
| 远程访问 | **Tailscale + ntfy** | V0.4，05-hitl.md 已选 |

V0.1 不做 docker / brew，等用户多了再补。

---

## 存储设计

### SQLite 文件布局

```
~/.agent-master/
├── config.toml             # 全局配置
├── state.db                # 主数据库（所有 7+4 个对象）
├── state.db-wal            # WAL
├── state.db-shm            # shared memory
├── secrets.db              # 单独 DB，权限 600（V0.5）
├── adapters/               # adapter-specific 缓存
│   ├── claude_code/
│   │   └── offsets.json    # 文件 tail 进度
│   ├── codex/
│   └── hermes/
├── workflows/              # YAML workflow 定义
│   └── *.yml
├── logs/
│   ├── daemon.log
│   └── adapters/
│       ├── claude_code.log
│       └── ...
└── artifacts/              # 工件存储（V0.3+）
    └── <run_id>/
```

### Schema 迁移

不引入 alembic 之类。手写：

```
core/migrations/
├── 001_initial.sql
├── 002_add_workflow.sql
├── 003_add_approval_checkpoint.sql
└── _runner.py
```

```python
def migrate(db: Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INT PRIMARY KEY)")
    current = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0

    for path in sorted(MIGRATIONS_DIR.glob("[0-9]*.sql")):
        version = int(path.stem.split("_")[0])
        if version > current:
            db.executescript(path.read_text())
            db.execute("INSERT INTO schema_version VALUES (?)", (version,))
            db.commit()
```

### Backup

每次 daemon 启动自动 backup：
```python
shutil.copy("state.db", f"state.db.backup.{date}")
# 保留最近 7 天
```

---

## API 设计

### 协议

- REST + SSE，不用 GraphQL（小项目过度设计）
- 路径前缀 `/api/v1`
- 错误格式统一 `{"error": "...", "code": "..."}`

### 主要端点（V0.1）

```
GET    /api/v1/agents                       列出所有 agent
GET    /api/v1/agents/:id                   单个 agent
GET    /api/v1/sessions                     列出 session（支持 filter）
GET    /api/v1/sessions/:id                 单个 session
GET    /api/v1/sessions/:id/events          事件流（分页）
GET    /api/v1/runs/:id                     run 详情
GET    /api/v1/topology                     当前拓扑图

# 实时
GET    /api/v1/stream/sessions              SSE 全局事件
GET    /api/v1/stream/sessions/:id          SSE 单 session
```

### V0.2 新增（编排）

```
POST   /api/v1/tasks                        创建 task
POST   /api/v1/tasks/:id/dispatch           派给 agent
PATCH  /api/v1/agents/:id                   修改 agent（pause 等）
POST   /api/v1/agents/:id/pause
POST   /api/v1/agents/:id/resume
```

### V0.4 新增（HITL）

```
POST   /api/v1/approve                      hook 调用入口
GET    /api/v1/approvals                    列出待审
POST   /api/v1/approvals/:id/decide         决策
GET    /api/v1/rules                        规则列表
POST   /api/v1/rules                        创建规则
```

### 认证

| 阶段 | 模式 | 说明 |
|------|------|------|
| V0.1 | 仅 127.0.0.1 | local-only，无认证 |
| V0.4 | bearer token | 远程访问引入 token |
| V0.5+ | OIDC 可选 | 多用户场景 |

---

## 配置文件

### `~/.agent-master/config.toml`

```toml
[daemon]
host = "127.0.0.1"
port = 8765
log_level = "info"

[storage]
db_path = "~/.agent-master/state.db"
backup_retention_days = 7

[ui]
auto_open_browser = true

[adapters]
enabled = ["claude_code", "codex", "hermes"]

[adapters.claude_code]
projects_dir = "~/.claude/projects"
poll_fallback_ms = 1000

[adapters.codex]
sessions_dir = "~/.codex/sessions"

[adapters.hermes]
db_path = "~/.hermes/state.db"
poll_ms = 200

# V0.4 +
[hitl]
default_timeout_minutes = 30
default_action = "reject"
remote_approval = false

[hitl.push]
provider = "ntfy"
topic = "agent-master-myhost"
```

---

## 安全模型

### V0.1 假设

- **机器是可信的** — local-first，machine 内任意用户都能访问 daemon
- **网络绑 127.0.0.1** — 防止局域网扫描
- **adapter 只读** — V0.1 不写 agent 数据

### V0.2 引入 Controller 时

- adapter spawn 进程要有审计 log
- 不允许 daemon 直接执行任意 shell（task description 走 agent，不走 shell）

### V0.4 远程访问

- 必须有 bearer token，绑定 IP / 失效时间
- token 存配置文件，权限 600
- HTTPS（Tailscale 自带，或 caddy reverse proxy）
- 所有 mutation 写 audit log

### V0.5 多机器

- 引入 client cert / mTLS
- 每台机器一个 daemon，通过 sync protocol 同步

---

## 日志

### 结构化日志

JSON line format，便于自己消费（吃自己的狗粮）：

```json
{"ts": "2026-06-07T10:00:00Z", "level": "info", "component": "adapter.claude_code",
 "event": "session_detected", "session_id": "abc", "duration_ms": 12}
```

### 日志级别

```
debug   — 开发/调试
info    — 重要事件（session 开始、approval 触发）
warn    — 可能问题（adapter 重启、polling 超时）
error   — 真错误（DB 锁失败、adapter 崩溃）
```

### 轮转

按天，保留 30 天。

---

## 可观测性（自己观察自己）

我们的 daemon 应该把自己的运行指标也作为一个 "agent" 暴露：

```
GET /api/v1/internal/metrics

{
  "uptime_seconds": 3600,
  "adapters": {
    "claude_code": {"status": "running", "events_processed": 1234, ...},
    "codex": {...}
  },
  "db": {"size_mb": 12, "events_count": 5678},
  "memory_mb": 156,
  "cpu_percent": 2.3
}
```

UI 上能看到 daemon 自身状态。

---

## CLI

```
agent-master start              启动 daemon
agent-master stop               停止
agent-master status             状态
agent-master logs               日志
agent-master config show        显示配置
agent-master adapter list       adapter 列表
agent-master adapter test <name>  测试 adapter 解析（用 fixture）
agent-master db backup           手动 backup
agent-master db migrate          手动迁移
```

---

## 测试策略

| 层 | 工具 | 覆盖率目标 |
|----|------|-----------|
| Unit | pytest | 70%+ |
| Adapter | pytest + fixture | 90%+ |
| API | httpx + pytest | 80%+ |
| Integration | docker-compose + 真 agent | 关键路径 |
| UI | playwright | smoke test only |

CI 用 GitHub Actions（V0.1 之后）。

---

## 依赖清单（V0.1 锁定）

### Python

```toml
[project]
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "watchfiles>=0.24",       # fsevents/inotify 跨平台
  "sse-starlette>=2.0",
  "pydantic>=2.9",
  "tomli-w>=1.0",
  "structlog>=24.0",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "httpx", "ruff"]
```

故意不引入：
- ❌ SQLAlchemy（schema 简单）
- ❌ Celery（asyncio 够）
- ❌ Redis（in-memory event bus 够）
- ❌ Alembic（手写迁移够）

### Frontend

详见 07-ui.md。

---

## 不做（V0.1）

- 集群 / 高可用（一台机器）
- 数据库切换（先 SQLite，不准备 Postgres 抽象层）
- 插件系统（adapter 已经够灵活）
- 国际化（先中英，按需）

---

下一份 07-ui.md 讲前端怎么搭。
