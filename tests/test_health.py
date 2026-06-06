"""Smoke tests for GET /api/v1/health."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_master import __version__
from agent_master.api.app import create_app


def test_health_returns_ok_and_version():
    client = TestClient(create_app())
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0


def test_health_uptime_grows_monotonically():
    app = create_app()
    client = TestClient(app)
    first = client.get("/api/v1/health").json()["uptime_seconds"]
    second = client.get("/api/v1/health").json()["uptime_seconds"]
    assert second >= first
