"""Tests for the new query + SSE endpoints (M1.4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_master.api.app import create_app
from agent_master.db import connect, migrate
from agent_master.models import Agent, Event, Run, Session
from agent_master.repo import AgentRepo, EventRepo, RunRepo, SessionRepo


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    with connect(db_path) as conn:
        migrate(conn)
        agent = AgentRepo(conn).create(Agent(name="Test agent", adapter_type="opencode"))
        session = SessionRepo(conn).create(
            Session(agent_id=agent.id, external_id="ses_test", workdir="/tmp/x")
        )
        run = RunRepo(conn).create(Run(session_id=session.id, trigger="manual"))
        erepo = EventRepo(conn)
        for i, kind in enumerate(["user_message", "tool_call", "tool_result"]):
            erepo.create(
                Event(run_id=run.id, seq=i, kind=kind, text=f"event {i}")
            )
    return db_path


def test_list_agents(seeded_db: Path):
    client = TestClient(create_app(db_path=seeded_db))
    resp = client.get("/api/v1/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 1
    assert agents[0]["name"] == "Test agent"
    assert agents[0]["adapter_type"] == "opencode"


def test_list_sessions(seeded_db: Path):
    client = TestClient(create_app(db_path=seeded_db))
    resp = client.get("/api/v1/sessions")
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 1
    assert sessions[0]["external_id"] == "ses_test"


def test_get_session_by_id(seeded_db: Path):
    client = TestClient(create_app(db_path=seeded_db))
    sessions = client.get("/api/v1/sessions").json()
    sid = sessions[0]["id"]
    resp = client.get(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["external_id"] == "ses_test"


def test_get_session_not_found(seeded_db: Path):
    client = TestClient(create_app(db_path=seeded_db))
    resp = client.get("/api/v1/sessions/no_such_id")
    assert resp.status_code == 404


def test_list_session_events(seeded_db: Path):
    client = TestClient(create_app(db_path=seeded_db))
    sid = client.get("/api/v1/sessions").json()[0]["id"]
    resp = client.get(f"/api/v1/sessions/{sid}/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 3
    assert [e["kind"] for e in events] == ["user_message", "tool_call", "tool_result"]


def test_list_run_events_with_kind_filter(seeded_db: Path):
    client = TestClient(create_app(db_path=seeded_db))
    sid = client.get("/api/v1/sessions").json()[0]["id"]
    events = client.get(f"/api/v1/sessions/{sid}/events").json()
    run_id = events[0]["run_id"]

    # Only tool_calls
    resp = client.get(f"/api/v1/runs/{run_id}/events?kind=tool_call")
    assert resp.status_code == 200
    kinds = [e["kind"] for e in resp.json()]
    assert kinds == ["tool_call"]


def test_list_run_events_after_seq(seeded_db: Path):
    client = TestClient(create_app(db_path=seeded_db))
    sid = client.get("/api/v1/sessions").json()[0]["id"]
    events = client.get(f"/api/v1/sessions/{sid}/events").json()
    run_id = events[0]["run_id"]

    resp = client.get(f"/api/v1/runs/{run_id}/events?after_seq=0")
    seqs = [e["seq"] for e in resp.json()]
    assert seqs == [1, 2]


def test_internal_metrics(seeded_db: Path):
    # `with TestClient(...)` triggers lifespan so app.state.broker/pipeline exist
    with TestClient(create_app(db_path=seeded_db)) as client:
        resp = client.get("/api/v1/internal/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "uptime_seconds" in body
        assert "pipeline" in body
        assert "broker" in body
        assert body["pipeline"]["events_received"] == 0


# Note: SSE streaming itself is covered at the broker+pipeline level
# (tests/test_broker.py + tests/test_pipeline.py). End-to-end SSE through
# the HTTP layer is verified by smoke_real_daemon.py — TestClient.stream
# hangs on long-lived SSE connections without explicit close logic.
