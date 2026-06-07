"""Generate a realistic in-database fixture for UI development.

Goals:
    - Every Event.kind appears at least once (so UI can style each)
    - Topology has at least one parent/child session pair
    - Status mix: active / idle / closed / error
    - Approvals queue has 2 pending items (V0.4 surface preview)

Run via CLI: agent-master seed [--reset]
Or via Python: from agent_master.seed import seed_mock_data
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ..db import connect, migrate
from ..models import (
    Agent,
    Approval,
    Artifact,
    Budget,
    Event,
    Rule,
    Run,
    Session,
)
from ..repo import (
    AgentRepo,
    ApprovalRepo,
    ArtifactRepo,
    BudgetRepo,
    EventRepo,
    RuleRepo,
    RunRepo,
    SessionRepo,
)

# ─── deterministic-ish realistic event text ─────────────────────────────

_USER_PROMPTS = [
    "Refactor src/auth/jwt.py to use jose instead of pyjwt",
    "Add a test for the regression in #4421",
    "Why is the build failing on macOS?",
    "Let's tackle the OAuth migration — start with reading the spec",
    "Generate release notes for v0.4.2 from the commit log",
]

_REASONING = [
    "First I need to read the current implementation to understand the contract...",
    "Looking at the test failure, the assertion happens after the mock is reset.",
    "There are three possible approaches. The cleanest is to extract the validator.",
    "The user wants a behavior change but the API surface stays the same.",
]

_TOOL_NAMES = ["Read", "Edit", "Write", "Bash", "Grep", "Glob", "Patch"]
_ASSISTANT_TEXTS = [
    "I'll read the existing implementation first, then propose a refactor.",
    "Found the bug — there's a missing await in `process_batch`. Let me fix it.",
    "All tests pass locally. Want me to run the integration suite too?",
    "Looking at this, three files need to change. I'll do them in order.",
]


def _make_event(run_id: str, seq: int, kind: str, ts: datetime,
                text: str | None = None, payload: dict | None = None) -> Event:
    return Event(run_id=run_id, seq=seq, kind=kind, ts=ts,
                 text=text, payload=payload or {})


def wipe_mock_data(db_path: Path) -> None:
    """Remove everything from the DB. Use with care."""
    with connect(db_path) as conn:
        migrate(conn)
        # Order matters — children first
        for table in ("audit_log", "approvals", "artifacts", "events",
                      "runs", "sessions", "rules", "budgets", "agents",
                      "tasks", "workflows"):
            conn.execute(f"DELETE FROM {table}")


def seed_mock_data(db_path: Path, *, reset: bool = False) -> dict:
    """Populate DB with realistic fixtures. Returns a small summary dict."""
    if reset:
        wipe_mock_data(db_path)

    rng = random.Random(42)  # deterministic
    now = datetime.now(timezone.utc)

    with connect(db_path) as conn:
        migrate(conn)
        arepo = AgentRepo(conn)
        srepo = SessionRepo(conn)
        rrepo = RunRepo(conn)
        erepo = EventRepo(conn)
        artrepo = ArtifactRepo(conn)
        aprepo = ApprovalRepo(conn)
        budrepo = BudgetRepo(conn)
        rulerepo = RuleRepo(conn)

        # ── 3 agents ──────────────────────────────────────────────────
        agents = {
            "opencode": arepo.create(Agent(
                name="OpenCode (Sisyphus)",
                adapter_type="opencode",
                adapter_config={"workdir": "/Users/horus/sideproject"},
                status="busy",
                capabilities=["code", "refactor", "test"],
            )),
            "claude_code": arepo.create(Agent(
                name="Claude Code on macbook",
                adapter_type="claude_code",
                adapter_config={"workdir": "/Users/horus"},
                status="idle",
                capabilities=["code", "review", "research"],
            )),
            "hermes": arepo.create(Agent(
                name="Hermes",
                adapter_type="hermes",
                adapter_config={"source": "cli"},
                status="busy",
                capabilities=["agent", "orchestrate"],
            )),
        }

        # ── sessions ──────────────────────────────────────────────────
        # Active session — parent
        s_parent = srepo.create(Session(
            agent_id=agents["opencode"].id,
            external_id="ses_active_parent_aaa",
            workdir="/Users/horus/sideproject/agent-master",
            started_at=now - timedelta(minutes=45),
            last_active_at=now - timedelta(seconds=30),
            status="active",
            summary="Implementing the M1.5 UI scaffolding",
            meta={"model": "claude-opus-4.7", "cost": 0.42},
        ))

        # 2 child sessions (sidechain-style)
        s_child1 = srepo.create(Session(
            agent_id=agents["claude_code"].id,
            external_id="ses_active_child_bbb",
            parent_session_id=s_parent.id,
            workdir="/Users/horus/sideproject/agent-master",
            started_at=now - timedelta(minutes=30),
            last_active_at=now - timedelta(minutes=2),
            status="active",
            summary="Spawned: code review of new components",
            meta={"model": "claude-haiku-4.5"},
        ))
        s_child2 = srepo.create(Session(
            agent_id=agents["hermes"].id,
            external_id="ses_active_child_ccc",
            parent_session_id=s_parent.id,
            workdir="/Users/horus/sideproject/agent-master",
            started_at=now - timedelta(minutes=25),
            last_active_at=now - timedelta(seconds=10),
            status="active",
            summary="Spawned: running the test suite",
            meta={"source": "cli"},
        ))

        # Idle session
        s_idle = srepo.create(Session(
            agent_id=agents["hermes"].id,
            external_id="ses_idle_ddd",
            workdir="/Users/horus/personal",
            started_at=now - timedelta(hours=2),
            last_active_at=now - timedelta(minutes=20),
            status="idle",
            summary="Earlier: drafted the weekly notes",
        ))

        # 2 closed sessions
        s_closed1 = srepo.create(Session(
            agent_id=agents["claude_code"].id,
            external_id="ses_closed_eee",
            workdir="/Users/horus/oss",
            started_at=now - timedelta(hours=4),
            last_active_at=now - timedelta(hours=3, minutes=15),
            ended_at=now - timedelta(hours=3, minutes=15),
            status="closed",
            summary="Closed: shipped the OAuth migration PR",
        ))
        s_closed2 = srepo.create(Session(
            agent_id=agents["opencode"].id,
            external_id="ses_closed_fff",
            workdir="/Users/horus/sideproject/electron-sprite",
            started_at=now - timedelta(hours=6),
            last_active_at=now - timedelta(hours=5),
            ended_at=now - timedelta(hours=5),
            status="closed",
            summary="Closed: investigated the perf regression (root cause found)",
        ))

        sessions = [s_parent, s_child1, s_child2, s_idle, s_closed1, s_closed2]

        # ── runs + events per session ─────────────────────────────────
        all_runs: list[Run] = []
        for sess in sessions:
            num_runs = rng.randint(1, 3)
            base_ts = sess.started_at
            for run_idx in range(num_runs):
                run_start = base_ts + timedelta(minutes=run_idx * 5)
                # Mix of statuses
                if sess.status == "closed":
                    run_status = rng.choice(["success", "success", "failed"])
                    run_end = run_start + timedelta(minutes=rng.randint(2, 10))
                elif run_idx == num_runs - 1 and sess.status == "active":
                    run_status = "running"
                    run_end = None
                else:
                    run_status = "success"
                    run_end = run_start + timedelta(minutes=rng.randint(2, 8))

                run = rrepo.create(Run(
                    session_id=sess.id,
                    trigger="manual" if run_idx == 0 else rng.choice(["spawn", "manual"]),
                    started_at=run_start,
                    ended_at=run_end,
                    status=run_status,
                    tokens_in=rng.randint(500, 5000),
                    tokens_out=rng.randint(200, 2000),
                    cost_usd=Decimal(str(round(rng.uniform(0.01, 0.5), 4))),
                    summary=rng.choice(_ASSISTANT_TEXTS)[:120],
                    error_message="Connection timeout to API" if run_status == "failed" else None,
                ))
                all_runs.append(run)

                # Events — a realistic turn shape
                seq = 0
                t = run_start
                _emit = lambda kind, text=None, payload=None, dt=2: (
                    erepo.create(_make_event(run.id, seq, kind, t, text, payload))
                )

                # session_start
                erepo.create(_make_event(run.id, seq, "session_start", t, "session started"))
                seq += 1; t += timedelta(seconds=2)
                erepo.create(_make_event(run.id, seq, "run_start", t, "run started"))
                seq += 1; t += timedelta(seconds=3)
                erepo.create(_make_event(run.id, seq, "user_message", t,
                                         rng.choice(_USER_PROMPTS)))
                seq += 1; t += timedelta(seconds=4)

                # A flurry of agent activity
                for _ in range(rng.randint(3, 8)):
                    erepo.create(_make_event(run.id, seq, "reasoning", t,
                                             rng.choice(_REASONING)))
                    seq += 1; t += timedelta(seconds=rng.randint(1, 5))
                    tool = rng.choice(_TOOL_NAMES)
                    erepo.create(_make_event(run.id, seq, "tool_call", t,
                                             f"{tool}(path='src/foo.ts')",
                                             {"tool": tool, "input": {"path": "src/foo.ts"}}))
                    seq += 1; t += timedelta(seconds=rng.randint(1, 3))
                    erepo.create(_make_event(run.id, seq, "tool_result", t,
                                             f"{tool} completed in {rng.randint(50, 800)}ms"))
                    seq += 1; t += timedelta(seconds=2)
                    if rng.random() < 0.3:
                        erepo.create(_make_event(run.id, seq, "assistant_message", t,
                                                 rng.choice(_ASSISTANT_TEXTS)))
                        seq += 1; t += timedelta(seconds=2)

                # Maybe an artifact mid-run
                if rng.random() < 0.5:
                    erepo.create(_make_event(run.id, seq, "artifact_created", t,
                                             "wrote src/foo.ts (+45/-12)"))
                    seq += 1; t += timedelta(seconds=1)

                # status_change once in a while
                if rng.random() < 0.2:
                    erepo.create(_make_event(run.id, seq, "status_change", t,
                                             "context window 80% used; compacting"))
                    seq += 1; t += timedelta(seconds=2)

                # Error event if the run failed
                if run_status == "failed":
                    erepo.create(_make_event(run.id, seq, "error", t,
                                             "Connection timeout to API",
                                             {"level": "error"}))
                    seq += 1; t += timedelta(seconds=1)

                # run_end and (only for very last run of a closed session) session_end
                if run_end is not None:
                    erepo.create(_make_event(run.id, seq, "run_end", run_end,
                                             f"completed ({run_status})"))
                    seq += 1

                base_ts = run_end or run_start + timedelta(minutes=10)

        # ── artifacts (4) ─────────────────────────────────────────────
        # Tie them to the most recent runs of the closed sessions (output-first)
        artifact_runs = [r for r in all_runs if r.status == "success"][:4] or all_runs[:4]
        artifacts = [
            Artifact(run_id=artifact_runs[0].id, kind="file",
                     title="src/auth/jwt.py", path="/repo/src/auth/jwt.py",
                     size_bytes=4521, meta={"lines_changed": 45}),
            Artifact(run_id=artifact_runs[1].id, kind="pr",
                     title="OAuth migration PR #1247",
                     path="https://github.com/example/repo/pull/1247",
                     meta={"status": "merged", "additions": 312, "deletions": 89}),
            Artifact(run_id=artifact_runs[2].id, kind="commit",
                     title="fix: regression in process_batch",
                     meta={"sha": "a1f4c92", "branch": "main"}),
            Artifact(run_id=artifact_runs[3].id, kind="document",
                     title="release-notes-v0.4.2.md",
                     path="/repo/release-notes-v0.4.2.md",
                     size_bytes=2840),
        ]
        for a in artifacts:
            artrepo.create(a)

        # ── 2 pending approvals (V0.4 surface preview) ────────────────
        for run, subj, detail, action in [
            (all_runs[0], "Allow `rm -rf node_modules`?",
             {"command": "rm -rf node_modules", "cwd": "/repo"}, "reject"),
            (all_runs[1], "Allow `git push --force origin main`?",
             {"command": "git push --force origin main", "branch": "main"}, "reject"),
        ]:
            aprepo.create(Approval(
                run_id=run.id, agent_id=run.session_id and agents["opencode"].id,
                subject=subj, detail=detail,
                default_action=action,
                requested_at=now - timedelta(minutes=3),
            ))

        # ── 1 budget + 1 sample rule ──────────────────────────────────
        budrepo.create(Budget(
            scope="global", period="day",
            limit_usd=Decimal("10.00"), spent_usd=Decimal("2.47"),
            limit_tokens=500000, spent_tokens=87420,
            on_exceed="warn",
        ))
        rulerepo.create(Rule(
            pattern="bash:npm test*", action="allow",
            scope="permanent",
        ))

        # ── summary ──────────────────────────────────────────────────
        summary = {
            "agents": arepo.count(),
            "sessions": srepo.count(),
            "runs": rrepo.count(),
            "events": erepo.count(),
            "artifacts": artrepo.count(),
            "approvals_pending": len(aprepo.list_pending()),
            "budgets": budrepo.count(),
            "rules": rulerepo.count(),
        }
        return summary
