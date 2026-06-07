"""Adapter system — extension point for any agent runtime.

Each adapter lives in `packages/adapters/<name>/` (or as an installed
plugin package) and declares one or more capabilities:

    - Observer: passively maps the agent's private data to our standard
      objects (Session, Run, Event). Read-only. The minimum contract.
    - Controller: actively dispatches tasks, pauses, resumes (V0.2+).
    - Approver: intercepts dangerous tool calls before they run (V0.4+).

V0.1 only requires Observer. The base classes for all three live here so
adapters can grow into them without rebuilds.
"""

from __future__ import annotations

from .base import Approver, Controller, Observer
from .registry import AdapterMetadata, AdapterRegistry, discover_adapters

__all__ = [
    "AdapterMetadata",
    "AdapterRegistry",
    "Approver",
    "Controller",
    "Observer",
    "discover_adapters",
]
