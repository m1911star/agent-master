"""Smoke tests for GET /api/v1/health."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_master import __version__
from agent_master.api.app import create_app


def test_health_returns_ok_and_version(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "state.db"))
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0


def test_health_uptime_grows_monotonically(tmp_path: Path):
    app = create_app(db_path=tmp_path / "state.db")
    client = TestClient(app)
    first = client.get("/api/v1/health").json()["uptime_seconds"]
    second = client.get("/api/v1/health").json()["uptime_seconds"]
    assert second >= first
