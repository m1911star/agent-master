"""Tests for the repository layer.

Each repo: create -> get -> list -> update -> delete roundtrip.
Plus per-repo specific queries (status filters, parent walking, etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_master.db import connect, migrate
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
from agent_master.repo import (
    AgentRepo,
    ApprovalRepo,
    ArtifactRepo,
    AuditLogRepo,
    BudgetRepo,
    EventRepo,
    RuleRepo,
    RunRepo,
    SessionRepo,
    TaskRepo,
    WorkflowRepo,
)


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "state.db"
    c = connect(db_path)
    migrate(c)
    try:
        yield c
    finally:
        c.close()


def test_agent_crud(conn):
    repo = AgentRepo(conn)
    a = repo.create(Agent(name="Claude", adapter_type="claude_code"))
    assert repo.exists(a.id)
    fetched = repo.get(a.id)
    assert fetched is not None
    assert fetched.name == "Claude"

    a.name = "Claude (renamed)"
    a.status = "busy"
    repo.update(a)
    assert repo.get(a.id).name == "Claude (renamed)"
    assert repo.get(a.id).status == "busy"

    busy = repo.list_by_status("busy")
    assert len(busy) == 1

    repo.delete(a.id)
    assert not repo.exists(a.id)
    assert repo.count() == 0


def test_session_crud_and_topology(conn):
    arepo = AgentRepo(conn)
    srepo = SessionRepo(conn)
    a = arepo.create(Agent(name="A", adapter_type="opencode"))
    parent = srepo.create(Session(agent_id=a.id, external_id="ses_p"))
    child = srepo.create(
        Session(agent_id=a.id, parent_session_id=parent.id, external_id="ses_c")
    )

    found = srepo.get_by_external_id("ses_p")
    assert found is not None and found.id == parent.id

    children = srepo.list_children(parent.id)
    assert len(children) == 1 and children[0].id == child.id

    actives = srepo.list_active(agent_id=a.id)
    assert len(actives) == 2


def test_run_and_events(conn):
    arepo = AgentRepo(conn)
    srepo = SessionRepo(conn)
    rrepo = RunRepo(conn)
    erepo = EventRepo(conn)

    a = arepo.create(Agent(name="A", adapter_type="hermes"))
    s = srepo.create(Session(agent_id=a.id))
    r = rrepo.create(Run(session_id=s.id, trigger="manual"))

    # max_seq on empty run is -1
    assert erepo.max_seq(r.id) == -1

    # Insert 3 events; ids should be auto-assigned by AUTOINCREMENT
    for i in range(3):
        e = erepo.create(Event(run_id=r.id, seq=i, kind="user_message", text=f"msg {i}"))
        assert e.id is not None and e.id > 0

    assert erepo.max_seq(r.id) == 2
    events = erepo.list_by_run(r.id)
    assert len(events) == 3
    assert [e.seq for e in events] == [0, 1, 2]

    # Filter by kind + after_seq
    after = erepo.list_by_run(r.id, after_seq=0, kind="user_message")
    assert [e.seq for e in after] == [1, 2]


def test_task_tree(conn):
    repo = TaskRepo(conn)
    parent = repo.create(Task(title="Big goal", created_by="user"))
    child = repo.create(
        Task(title="Subgoal", parent_task_id=parent.id, created_by="agent:foo")
    )
    children = repo.list_children(parent.id)
    assert len(children) == 1 and children[0].id == child.id

    pending = repo.list_by_status("pending")
    assert len(pending) == 2


def test_approval_pending_queue(conn):
    arepo = AgentRepo(conn)
    srepo = SessionRepo(conn)
    rrepo = RunRepo(conn)
    aprepo = ApprovalRepo(conn)
    a = arepo.create(Agent(name="A", adapter_type="claude_code"))
    s = srepo.create(Session(agent_id=a.id))
    r = rrepo.create(Run(session_id=s.id, trigger="manual"))

    p = aprepo.create(
        Approval(run_id=r.id, agent_id=a.id, subject="Allow rm?", default_action="reject")
    )
    pending = aprepo.list_pending()
    assert len(pending) == 1 and pending[0].id == p.id


def test_artifact_by_run(conn):
    arepo = AgentRepo(conn)
    srepo = SessionRepo(conn)
    rrepo = RunRepo(conn)
    artrepo = ArtifactRepo(conn)
    a = arepo.create(Agent(name="A", adapter_type="opencode"))
    s = srepo.create(Session(agent_id=a.id))
    r = rrepo.create(Run(session_id=s.id, trigger="manual"))
    art = artrepo.create(Artifact(run_id=r.id, kind="file", title="foo.ts"))
    found = artrepo.list_by_run(r.id)
    assert len(found) == 1 and found[0].id == art.id


def test_budget_scope_filter(conn):
    repo = BudgetRepo(conn)
    repo.create(Budget(scope="agent", period="day"))
    repo.create(Budget(scope="agent", period="month"))
    repo.create(Budget(scope="global", period="total"))
    assert len(repo.list_by_scope("agent")) == 2
    assert len(repo.list_by_scope("global")) == 1


def test_rule_global_and_agent_scoped(conn):
    arepo = AgentRepo(conn)
    rrepo = RuleRepo(conn)
    a = arepo.create(Agent(name="A", adapter_type="claude_code"))

    rrepo.create(Rule(pattern="rm -rf *", action="deny"))  # global
    rrepo.create(Rule(agent_id=a.id, pattern="npm test*", action="allow"))

    for_agent = rrepo.list_for_agent(a.id)
    assert len(for_agent) == 2  # both global + agent-scoped

    only_global = rrepo.list_for_agent(None)
    assert len(only_global) == 1


def test_workflow_versioning(conn):
    repo = WorkflowRepo(conn)
    repo.create(Workflow(name="dev", definition_yaml="v1", version=1))
    repo.create(Workflow(name="dev", definition_yaml="v2", version=2))
    latest = repo.get_by_name("dev")
    assert latest is not None and latest.version == 2 and latest.definition_yaml == "v2"


def test_audit_log_target_lookup(conn):
    repo = AuditLogRepo(conn)
    repo.create(
        AuditLog(actor="user", action="agent.create", target_type="agent", target_id="a1")
    )
    repo.create(
        AuditLog(actor="user", action="agent.update", target_type="agent", target_id="a1")
    )
    repo.create(
        AuditLog(actor="user", action="task.create", target_type="task", target_id="t1")
    )

    for_a1 = repo.list_for_target("agent", "a1")
    assert len(for_a1) == 2
    recent = repo.list_recent(limit=10)
    assert len(recent) == 3
