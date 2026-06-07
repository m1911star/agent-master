"""Tests for the adapter registry + base classes."""

from __future__ import annotations

from pathlib import Path

from agent_master.adapters import (
    AdapterMetadata,
    AdapterRegistry,
    Approver,
    Controller,
    Observer,
    discover_adapters,
)
from agent_master.adapters.base import SessionDescriptor


def test_session_descriptor_minimal():
    d = SessionDescriptor(external_id="ses_xyz")
    assert d.external_id == "ses_xyz"
    assert d.workdir == ""


def test_observer_is_abstract():
    """Observer cannot be instantiated without implementing abstract methods."""
    try:
        Observer()  # type: ignore[abstract]
    except TypeError:
        return
    raise AssertionError("Observer should be abstract")


def test_controller_is_abstract():
    try:
        Controller()  # type: ignore[abstract]
    except TypeError:
        return
    raise AssertionError("Controller should be abstract")


def test_approver_is_abstract():
    try:
        Approver()  # type: ignore[abstract]
    except TypeError:
        return
    raise AssertionError("Approver should be abstract")


def test_adapter_metadata_from_toml(tmp_path: Path):
    pkg = tmp_path / "claude_code"
    pkg.mkdir()
    (pkg / "adapter.toml").write_text(
        '[adapter]\n'
        'name = "claude_code"\n'
        'version = "0.1.0"\n'
        'display_name = "Claude Code"\n\n'
        '[capabilities]\n'
        'observer = true\n'
        'controller = false\n'
        'approver = false\n\n'
        '[observer]\n'
        'watch_paths = ["~/.claude/projects/**/*.jsonl"]\n'
        'poll_interval_ms = 1000\n'
    )

    meta = AdapterMetadata.from_toml(pkg / "adapter.toml")
    assert meta.name == "claude_code"
    assert meta.version == "0.1.0"
    assert meta.display_name == "Claude Code"
    assert meta.has("observer") is True
    assert meta.has("controller") is False
    assert meta.observer_config["poll_interval_ms"] == 1000
    assert meta.path == pkg


def test_adapter_registry_filters_by_capability(tmp_path: Path):
    # Create two fake adapter packages
    for name in ("opencode", "hermes"):
        pkg = tmp_path / name
        pkg.mkdir()
        (pkg / "adapter.toml").write_text(
            f'[adapter]\nname = "{name}"\nversion = "0.1.0"\n\n'
            f'[capabilities]\nobserver = true\ncontroller = false\n'
        )
    # And one with controller too
    pkg = tmp_path / "claude_code"
    pkg.mkdir()
    (pkg / "adapter.toml").write_text(
        '[adapter]\nname = "claude_code"\nversion = "0.1.0"\n\n'
        '[capabilities]\nobserver = true\ncontroller = true\n'
    )

    registry = discover_adapters([tmp_path])
    assert len(registry.all()) == 3
    assert {m.name for m in registry.with_capability("observer")} == {
        "opencode",
        "hermes",
        "claude_code",
    }
    assert {m.name for m in registry.with_capability("controller")} == {"claude_code"}


def test_adapter_registry_handles_missing_root(tmp_path: Path):
    nonexistent = tmp_path / "no_such_dir"
    registry = discover_adapters([nonexistent])
    assert registry.all() == []
