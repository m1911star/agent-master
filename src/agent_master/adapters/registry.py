"""Adapter registry — discovers and instantiates adapter packages.

Per doc/02-adapter.md §Adapter 注册机制. Each adapter lives in
`packages/adapters/<name>/` with an `adapter.toml` manifest.

For V0.1, this module loads metadata from disk but doesn't instantiate
implementations (those land with M1.3 alongside the actual adapter code).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AdapterMetadata:
    name: str
    version: str
    display_name: str = ""
    capabilities: dict[str, bool] = field(default_factory=dict)
    observer_config: dict[str, Any] = field(default_factory=dict)
    controller_config: dict[str, Any] = field(default_factory=dict)
    approver_config: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None  # location on disk

    @classmethod
    def from_toml(cls, path: Path) -> AdapterMetadata:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        adapter = data.get("adapter", {})
        return cls(
            name=adapter.get("name", path.parent.name),
            version=adapter.get("version", "0.0.0"),
            display_name=adapter.get("display_name", ""),
            capabilities=data.get("capabilities", {}),
            observer_config=data.get("observer", {}),
            controller_config=data.get("controller", {}),
            approver_config=data.get("approver", {}),
            path=path.parent,
        )

    def has(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))


class AdapterRegistry:
    """In-memory registry of discovered adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, AdapterMetadata] = {}

    def register(self, meta: AdapterMetadata) -> None:
        self._adapters[meta.name] = meta

    def get(self, name: str) -> AdapterMetadata | None:
        return self._adapters.get(name)

    def all(self) -> list[AdapterMetadata]:
        return list(self._adapters.values())

    def with_capability(self, capability: str) -> list[AdapterMetadata]:
        return [m for m in self._adapters.values() if m.has(capability)]


def discover_adapters(roots: Iterable[Path]) -> AdapterRegistry:
    """Scan one or more roots for `*/adapter.toml` and register each.

    A 'root' is a directory containing one subdir per adapter, e.g.:

        packages/adapters/
        ├── opencode/
        │   └── adapter.toml
        └── claude_code/
            └── adapter.toml
    """
    registry = AdapterRegistry()
    for root in roots:
        if not root.exists():
            continue
        for toml in sorted(root.glob("*/adapter.toml")):
            registry.register(AdapterMetadata.from_toml(toml))
    return registry
