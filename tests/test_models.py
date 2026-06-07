"""Roundtrip tests for all 11 dataclasses through to_row/from_row.

Doesn't hit a real DB — just verifies the serialization layer is consistent.
DB-level integration is in test_migrations.py and test_repos.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from agent_master.models import (
    Agent,
    Approval,
    Artifact,
    AuditLog,
    Budget,
    Event,
    Rule,
    Run,
    Session,
    Task,
    Workflow,
)


def _now() -> datetime:
    return datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


def test_agent_roundtrip():
    a = Agent(
        name="Claude on macbook",
        adapter_type="claude_code",
        adapter_config={"workdir": "/tmp"},
        capabilities=["code", "review"],
    )
    b = Agent.from_row(a.to_row())
    assert b.name == a.name
    assert b.adapter_type == a.adapter_type
    assert b.adapter_config == {"workdir": "/tmp"}
    assert b.capabilities == ["code", "review"]
    assert b.status == "idle"


def test_session_roundtrip():
    s = Session(
        agent_id="a1",
        external_id="ses_xyz",
        parent_session_id="s_parent",
        workdir="/repo",
        meta={"project": "foo"},
    )
    b = Session.from_row(s.to_row())
    assert b.external_id == "ses_xyz"
    assert b.parent_session_id == "s_parent"
    assert b.meta == {"project": "foo"}


def test_run_roundtrip_decimal_cost():
    r = Run(
        session_id="s1",
        trigger="manual",
        tokens_in=100,
        tokens_out=200,
        cost_usd=Decimal("0.1234"),
    )
    b = Run.from_row(r.to_row())
    assert b.tokens_in == 100
    assert b.cost_usd == Decimal("0.1234")
    assert b.trigger == "manual"


def test_event_roundtrip():
    e = Event(
        run_id="r1",
        seq=42,
        kind="tool_call",
        text="Bash(npm test)",
        payload={"tool": "Bash", "input": {"cmd": "npm test"}},
        stream="agent",
        level="info",
    )
    b = Event.from_row(e.to_row())
    assert b.seq == 42
    assert b.kind == "tool_call"
    assert b.payload == {"tool": "Bash", "input": {"cmd": "npm test"}}


def test_task_roundtrip():
    t = Task(
        title="Implement X",
        description="Long description...",
        parent_task_id="t_parent",
        priority=80,
        goal_chain=["company-goal", "sub-goal"],
        created_by="user",
    )
    b = Task.from_row(t.to_row())
    assert b.title == "Implement X"
    assert b.priority == 80
    assert b.goal_chain == ["company-goal", "sub-goal"]


def test_approval_roundtrip_with_checkpoint():
    a = Approval(
        run_id="r1",
        agent_id="a1",
        subject="Allow rm -rf node_modules?",
        detail={"command": "rm -rf node_modules", "diff": None},
        checkpoint_data={"state": "serialized blob"},
        default_action="reject",
    )
    b = Approval.from_row(a.to_row())
    assert b.subject == a.subject
    assert b.detail == {"command": "rm -rf node_modules", "diff": None}
    assert b.checkpoint_data == {"state": "serialized blob"}
    assert b.status == "pending"
    assert b.default_action == "reject"


def test_artifact_roundtrip():
    art = Artifact(
        run_id="r1",
        kind="file",
        title="foo.ts",
        path="/tmp/foo.ts",
        content_hash="sha256:abc",
        size_bytes=1024,
        meta={"lines": 50},
    )
    b = Artifact.from_row(art.to_row())
    assert b.kind == "file"
    assert b.size_bytes == 1024
    assert b.meta == {"lines": 50}


def test_budget_roundtrip_decimal_limit():
    bu = Budget(
        scope="agent",
        period="day",
        limit_usd=Decimal("5.00"),
        spent_usd=Decimal("1.23"),
        on_exceed="pause",
    )
    b = Budget.from_row(bu.to_row())
    assert b.limit_usd == Decimal("5.00")
    assert b.spent_usd == Decimal("1.23")
    assert b.on_exceed == "pause"


def test_rule_roundtrip():
    r = Rule(
        agent_id="a1",
        pattern="bash:rm -rf *",
        action="deny",
        scope="permanent",
    )
    b = Rule.from_row(r.to_row())
    assert b.pattern == "bash:rm -rf *"
    assert b.action == "deny"


def test_workflow_roundtrip():
    w = Workflow(
        name="feature-dev",
        definition_yaml="nodes: []\n",
        version=1,
    )
    b = Workflow.from_row(w.to_row())
    assert b.name == "feature-dev"
    assert b.version == 1


def test_audit_log_roundtrip():
    al = AuditLog(
        actor="user",
        action="agent.create",
        target_type="agent",
        target_id="a1",
        payload={"name": "Claude"},
        ip_addr="127.0.0.1",
    )
    b = AuditLog.from_row(al.to_row())
    assert b.actor == "user"
    assert b.action == "agent.create"
    assert b.payload == {"name": "Claude"}
    assert b.ip_addr == "127.0.0.1"


def test_to_dict_returns_serializable():
    """to_dict() should be JSON-friendly (no datetime, no Decimal as raw type)."""
    import json

    a = Agent(name="x", adapter_type="claude_code")
    json.dumps(a.to_dict())  # must not raise

    r = Run(session_id="s1", trigger="manual", cost_usd=Decimal("0.5"))
    s = json.dumps(r.to_dict())
    assert "0.5" in s
