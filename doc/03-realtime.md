# 03 · 实时观测层

> 状态：草案 · spike 已验证延迟可行性，这里把它产品化

## 核心结论（spike 实测）

| 项目 | 中位延迟 | 含义 |
|------|---------|------|
| fsevents 文件通知（macOS） | **1ms（P90 3ms）** | 几乎零延迟 |
| Codex 自己 flush 节奏 | **14-17ms** | 文件 watch = token 级实时 |
| Claude Code flush 节奏 | 866-988ms | 行级实时（每秒一动作） |
| Hermes SQLite WAL | 不通知，需 poll | 设 200ms poll = "近实时" |

**结论：实时被现实劈成三档，每档不同实现路径。**

## 三档实时模型

### L1 · 状态级（1-3 秒）— 80% 老板看板需求

**手段：** fsevents/inotify + 防抖（debounce 300ms）。

**能看到：**
- 哪个 agent 在工作 / 闲置
- session 列表 + 摘要
- 进度条 / token 计数（5s 更新一次）

**用户体验：** 你扫一眼"哦它还在干活"。

**实现成本：低** — V0.1 必做。

---

### L2 · 动作级（亚秒）— 用户嘴里"实时"的真实需求

**手段：** L1 + 直接 stream 高频 flush（Codex）+ 对 Claude 用 raw fs.watch。

**能看到：**
- agent 正在调用什么工具
- 工具结果一返回就出现
- 文字输出一行一行刷出

**用户体验：** 像看 Claude Code TUI 但跨多个 agent。

**实现成本：中** — V0.1 必做（spike 已证可行）。

---

### L3 · Token 级（毫秒）— 性能/调试用

**手段：** L2 + tail 文件 raw bytes，按 chunk 推 SSE。

**能看到：**
- 思考一个字一个字浮现
- 工具参数生成中

**用户体验：** debug agent prompt 时极有用，日常负担过重。

**实现成本：高** — V0.3+ 可选。

---

## 实现栈

### Backend

```
┌─────────────────────────────────────┐
│  Adapter (Observer)                 │
│  - fsevents/inotify watcher          │
│  - SQLite WAL poller                 │
│  - 标准化为 Event                    │
└──────────────┬──────────────────────┘
               │ in-process queue
               ▼
┌─────────────────────────────────────┐
│  Event Pipeline                     │
│  - 去重 (run_id, seq)               │
│  - 防抖（同 session 300ms 合并）     │
│  - 持久化到 SQLite (WAL mode)        │
│  - fanout 到订阅者                   │
└──────────────┬──────────────────────┘
               │
        ┌──────┴──────┐
        ▼             ▼
   ┌─────────┐   ┌─────────┐
   │ SQLite  │   │ SSE/WS  │
   │  store  │   │ broker  │
   └─────────┘   └─────────┘
                      │
                      ▼
                 ┌─────────┐
                 │   UI    │
                 └─────────┘
```

### 三档对应的传输

| 档位 | 传输 | 频率上限 |
|------|------|---------|
| L1 状态 | SSE，1Hz | 1 帧/秒 |
| L2 动作 | SSE，10Hz | 10 帧/秒，事件触发 |
| L3 Token | WebSocket | 实时 push，无节流 |

---

## fsevents 实现注意

### macOS

```python
from fsevents import Observer, Stream

def on_change(event):
    # event.name = 文件路径
    # event.mask = 操作（IN_MODIFY, IN_CREATE 等）
    pipeline.notify(event.name)

observer = Observer()
observer.schedule(Stream(on_change, *paths, file_events=True))
observer.start()
```

**坑：**
- 默认 latency=0.5s，要设到 0 才能拿到 spike 那个 1ms 数据
- file_events=True 才会通知文件级别（默认只通知目录）
- 路径必须存在，新文件路径要监听父目录

### Linux

```python
import inotify_simple as inotify

i = inotify.INotify()
watch_flags = inotify.flags.MODIFY | inotify.flags.CREATE
for path in paths:
    i.add_watch(path, watch_flags)

for event in i.read():
    pipeline.notify(event)
```

### 跨平台抽象

写个统一接口 `core/watch.py`：

```python
class Watcher:
    def watch(self, paths: list[Path], callback: Callable[[Path], None]) -> None: ...
    def stop(self) -> None: ...

def make_watcher() -> Watcher:
    if sys.platform == "darwin":
        return FSEventsWatcher()
    if sys.platform == "linux":
        return InotifyWatcher()
    return PollingWatcher(interval=1.0)  # fallback
```

---

## SQLite WAL tail（Hermes 用）

```python
class SqliteTailer:
    def __init__(self, db_path: Path, table: str, ts_col: str = "created_at"):
        self.db = sqlite3.connect(db_path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.last_seen_id = self._max_id()

    def poll(self) -> list[dict]:
        rows = self.db.execute(
            f"SELECT * FROM {self.table} WHERE id > ? ORDER BY id",
            (self.last_seen_id,)
        ).fetchall()
        if rows:
            self.last_seen_id = rows[-1]["id"]
        return rows
```

**坑：**
- 只读连接（防止把别人的 DB 改坏）
- WAL 模式下另一个进程的写不会立刻反映，需要 `PRAGMA wal_checkpoint(PASSIVE)` 或干脆 reconnect
- poll 间隔 200ms 是平衡点（太短 CPU 高，太长不够实时）

---

## 增量读 JSONL 文件

每个 agent 的 jsonl 文件可能几 MB，不能每次全读。

```python
class JsonlTail:
    def __init__(self, path: Path):
        self.path = path
        self.offset = 0

    def read_new(self) -> list[dict]:
        with self.path.open("rb") as f:
            f.seek(self.offset)
            data = f.read()
            self.offset = f.tell()

        # 可能有不完整的最后一行
        lines = data.split(b"\n")
        if not data.endswith(b"\n"):
            # 回退到最后一个换行
            incomplete = lines.pop()
            self.offset -= len(incomplete)

        return [json.loads(l) for l in lines if l.strip()]
```

**坑：**
- 文件被 truncate / rotate 要处理（用 inode 判断）
- json 一行可能被拆开（看上面的 incomplete 处理）

---

## 防抖与降噪

UI 不需要每个 byte 都推，但事件不能丢。

**两层防抖：**

```python
class DebouncedFanout:
    """每 session 在 300ms 内合并多个事件，只 push 最新状态摘要。
    但完整事件流照常写入 DB。"""

    def __init__(self):
        self.pending: dict[str, list[Event]] = {}
        self.timers: dict[str, asyncio.Handle] = {}

    def notify(self, event: Event):
        # 1. 立刻持久化
        db.write_event(event)

        # 2. 防抖后再推 UI
        sid = event.session_id
        self.pending.setdefault(sid, []).append(event)
        if sid not in self.timers:
            self.timers[sid] = asyncio.get_event_loop().call_later(
                0.3, self._flush, sid
            )

    def _flush(self, sid: str):
        events = self.pending.pop(sid, [])
        del self.timers[sid]
        # push 一次摘要 + 全部事件 ids
        sse.broadcast(channel=f"session:{sid}", data={
            "latest_event": events[-1].to_dict(),
            "event_count": len(events),
            "event_ids": [e.id for e in events],
        })
```

UI 拿到通知后，按需 fetch 完整 events。

---

## 性能预算（V0.1 锁定）

| 指标 | 目标 |
|------|------|
| 空闲时 CPU | < 1% |
| 单 session 活跃时 CPU | < 5% |
| 5 个 session 同时活跃 CPU | < 15% |
| 内存常驻 | < 200MB |
| L2 事件到 UI P99 延迟 | < 500ms |
| SQLite 写入 P99 | < 50ms |

**怎么保证：** spike 已经在这些指标内。产品化要主动 benchmark，详见 06-architecture.md。

---

## 容错

| 失败 | 处理 |
|------|------|
| Adapter 进程崩溃 | core 重启它，重启后从 last_seen 恢复 |
| fsevents 丢通知 | 兜底 30s poll 全扫一次 |
| SQLite 锁住 | 重试，超过 5 次写错误日志 |
| 文件被 rotate | inode 变化检测，重新打开 |
| UI 断开 | SSE 自动重连，重连后 fetch 错过的 events |

---

## 不做（V0.1）

- 跨机器同步（V0.5+）
- 历史事件压缩（先 SQLite 撑住，撑不住再说）
- 自适应采样（先固定档位）

---

下一份 04-topology.md 讲"事件流和 session 拓扑怎么变成可视化的图"。
