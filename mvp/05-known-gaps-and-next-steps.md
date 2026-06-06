# 05 · Known Gaps & Next-Step Backlog

## Known issues (current spike state)

Bucketed by "user-visible vs not".

### 🟡 User-visible but tolerable

| Issue | Symptom | Fix cost |
|---|---|---|
| Run card cwd=? | Old hermes sessions never recorded cwd; Claude/Codex backfill reads last 8KB and misses session header | Add backfill-first-line logic, 30 min |
| Topology root node position reversed | In dagre TB mode, root ends up at the chain tail | Switch to BT (bottom-to-top), or add `align: 'UL'`, 10 min |
| Top-of-list hermes chain labels overlap | "Hermes Agent / Hermes Agent / ..." multiple same-name chains stack | Disambiguate with `(short-id)` suffix, 15 min |
| Live event stream long Chinese text doesn't truncate | Single event can wrap two-three lines | Add `max-height: 60px` + ellipsis, 5 min |
| `LIVE: 0` but stale exists | "live" = event within 30s; stale = after 4h | Document the thresholds, or add hover tooltip |

### 🔴 Invisible but known buggy

| Issue | Impact |
|---|---|
| Hermes / Claude double-counting | When Hermes proxies Claude, the same session is counted by both adapters | Low ROI to fix now, leave it |
| `monotonicSeq` cobbled from timestamp | Cross-source ordering may be wrong when multiple events share a ms | Affects very few cases |
| Codex `encrypted_content` annotation incomplete | Dashboard only shows "(encrypted)", not "encrypted thought, N tokens" | Add token estimate |
| OpenCode large tool_output not parsed | `~/.local/share/opencode/tool-output/` files not read | Dashboard only sees state.output |
| omp snapshot directory ignored | `~/.omp/agent/sessions/<...>/<id>/` directory not parsed | Doesn't affect run cards, just no file-snapshot view |

### 🟢 Deliberately not done (revisit later)

| Not doing | Reason |
|---|---|
| Events don't persist | Spike doesn't need replay; 500-line ring is enough for demo |
| No Turn intermediate tier | Steps attaching to run directly is sufficient |
| No `root_run_id` stored | Walking parent chain is fine |
| No SystemStep parsing | Only power users need it |
| No token-level live (L3) | Needs HTTP proxy or SDK push, next phase |
| No SQLite events.db | Will add when splitting at v0.1 |

## Short-term backlog (sorted by ROI)

### Sprint 1 — visual polish (one evening)

1. **Fix cwd=?**: backfill reads the first line, not just the last 8KB
2. **Fix topology root position**: try different `rankDir`/`align` combos
3. **Collapse long events**: max-height + expand button
4. **Default status filter to include stale**: currently stale is off by default, but new users get confused when they see "nothing"

### Sprint 2 — interaction upgrade (half day)

5. **In-events search box**: when a run is selected, grep keywords within its events
6. **Multi-select**: cmd-click to focus multiple runs side-by-side
7. **Deep-link back to source**: right-click card → "Open in Claude Code / Cursor / VSCode"
8. **Export a run**: dump a single run's full event log to JSON or markdown

### Sprint 3 — solve double-counting and merging (one day)

9. **Hermes proxy dedup**: fuzzy match by `cwd + startedAt ± 5s`, mark one run as a mirror of another
10. **Cross-source handoff restoration**: Hermes's handoff_state is actually a Claude/Codex relay; render as cross-source edge
11. **Add Cursor / Aider / new sources**: follow §02 template

### Sprint 4 — true "dashboard features" (one week)

12. **Replay / time machine**: left-side timeline scrubber; drag back to any state
13. **Trigger rule engine**: 50-line JS DSL (`witness.on('tool_call', e => e.toolName === 'rm -rf', notify)`)
14. **AI work-hours log**: scheduled job generates "AI did N things for you today" report
15. **Event persistence**: SQLite events.db + cross-restart replay

## Long-term direction (quarterly)

- **L3 real-time**: local HTTPS proxy + SSE decode → token-level stream
- **Behavior pattern analysis**: train a small model to classify "high-output session" vs "stuck session"
- **Cross-agent working memory**: every new agent start injects "X happened in this cwd before"
- **Menubar app (Tauri)**: no need to open a browser
- **Open source**: extract adapter interface + AgentEvent schema as a standalone package; open the adapter SDK first to invite community Cursor / Aider / etc. integrations

## Risks and uncertainties

- **Source schema changes**: every agent CLI is iterating; jsonl/SQLite formats may suddenly change (omp v15 → v16) → adapters should be defensive with try/except; errors must not crash the whole poller
- **Privacy concerns**: tool input/output leaks passwords / private chats / customer source code. The dashboard never expands by default and never uploads anywhere. But must call this out in the README
- **OpenCode subagent explosion**: measured 1 research task spawns 7 librarian subagents; if a future tool fans out 50–100, the layout algorithm needs a "collapse subtree" feature
- **Dagre performance over 500 nodes**: spike has 943 runs but only renders top 200 connected + 60 orphans; hasn't tested 500+ visible
- **Cross-source timestamp drift**: different agent CLIs running on different timezones / system clocks may have several seconds of skew. lastEventAt falls back to local wall clock
