"""agent-master: local-first observability for coding agents.

Public surface is intentionally thin during V0.1 — most callers should use the
CLI (`agent-master ...`) or hit the HTTP API on 127.0.0.1:8765.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-master")
except PackageNotFoundError:  # not installed (e.g. running from source tree)
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
