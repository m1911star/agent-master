"""Daemon lifecycle: start (foreground) / stop / status.

V0.1 keeps things deliberately simple: `start` runs uvicorn in the foreground
and writes a pidfile so `stop` can SIGTERM it from another shell. The user is
expected to background it (`agent-master start &`) or run under launchd/systemd
later. No double-fork daemonization — that's a recipe for portability pain we
don't need yet.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from .api.app import create_app
from .config import Config, ensure_config_dir, load_config
from .db import connect, migrate
from .logging_setup import configure_logging, get_logger

PIDFILE_NAME = "daemon.pid"


@dataclass
class DaemonStatus:
    running: bool
    pid: int | None
    pidfile: Path

    def describe(self) -> str:
        if self.running and self.pid is not None:
            return f"agent-master daemon running (pid {self.pid})"
        if self.pid is not None:
            return f"stale pidfile at {self.pidfile} (pid {self.pid} not alive)"
        return "agent-master daemon not running"


def pidfile_path(config: Config) -> Path:
    return ensure_config_dir(config.path.parent) / PIDFILE_NAME


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't own it — treat as alive.
        return True
    return True


def read_pidfile(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def status(config: Config | None = None) -> DaemonStatus:
    cfg = config or load_config()
    pf = pidfile_path(cfg)
    pid = read_pidfile(pf)
    if pid is None:
        return DaemonStatus(running=False, pid=None, pidfile=pf)
    return DaemonStatus(running=_pid_alive(pid), pid=pid, pidfile=pf)


def stop(config: Config | None = None, *, timeout: float = 10.0) -> bool:
    """Send SIGTERM to the running daemon, wait up to `timeout` seconds.

    Returns True if a process was stopped (or wasn't running), False if we
    timed out waiting for shutdown.
    """
    cfg = config or load_config()
    pf = pidfile_path(cfg)
    pid = read_pidfile(pf)
    log = get_logger("daemon")

    if pid is None:
        log.info("stop_noop", reason="no_pidfile")
        return True
    if not _pid_alive(pid):
        log.info("stop_noop", reason="stale_pidfile", pid=pid)
        pf.unlink(missing_ok=True)
        return True

    log.info("stop_signalling", pid=pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pf.unlink(missing_ok=True)
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            pf.unlink(missing_ok=True)
            log.info("stopped", pid=pid)
            return True
        time.sleep(0.1)

    log.warning("stop_timeout", pid=pid, timeout_seconds=timeout)
    return False


def _write_pidfile(path: Path) -> None:
    path.write_text(f"{os.getpid()}\n")


def start(config: Config | None = None) -> int:
    """Run the daemon in the foreground. Blocks until SIGTERM/SIGINT.

    Returns the process exit code (0 on clean shutdown).
    """
    cfg = config or load_config()
    configure_logging(cfg.daemon.log_level)
    log = get_logger("daemon")

    pf = pidfile_path(cfg)
    existing = read_pidfile(pf)
    if existing is not None and _pid_alive(existing):
        log.error("start_aborted", reason="already_running", pid=existing)
        print(
            f"agent-master daemon already running (pid {existing}). "
            "Run `agent-master stop` first.",
            file=sys.stderr,
        )
        return 1

    # Run migrations before binding the port — fail fast if DB is wedged.
    with connect(cfg.storage.db_path) as conn:
        applied = migrate(conn)
        if applied:
            log.info("db_migrated", applied=applied)
        else:
            log.info("db_ready", db_path=str(cfg.storage.db_path))

    _write_pidfile(pf)
    log.info(
        "starting",
        host=cfg.daemon.host,
        port=cfg.daemon.port,
        pidfile=str(pf),
        pid=os.getpid(),
    )

    app = create_app()
    try:
        uvicorn.run(
            app,
            host=cfg.daemon.host,
            port=cfg.daemon.port,
            log_config=None,  # we own logging via structlog
            access_log=False,
        )
    finally:
        # uvicorn.run() handles SIGTERM/SIGINT internally; we just clean up.
        try:
            if read_pidfile(pf) == os.getpid():
                pf.unlink(missing_ok=True)
        except OSError:
            pass
        log.info("stopped_self")

    return 0
