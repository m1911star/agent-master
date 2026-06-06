"""`agent-master` CLI entry point.

Using click (over typer) — fewer transitive deps, same ergonomics for the
small surface we need in V0.1. Will revisit if subcommands explode.
"""

from __future__ import annotations

import sys

import click

from . import __version__
from . import daemon as daemon_mod
from .config import DEFAULT_CONFIG_PATH, load_config
from .logging_setup import configure_logging


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="agent-master")
def cli() -> None:
    """agent-master — local-first dashboard for every coding agent on your machine."""


@cli.command()
def start() -> None:
    """Start the daemon (foreground; pair with & to detach)."""
    cfg = load_config()
    # configure_logging is called again inside daemon.start(), but doing it
    # here too means we get JSON logs for the pre-bind path (migrations etc.)
    configure_logging(cfg.daemon.log_level)
    exit_code = daemon_mod.start(cfg)
    sys.exit(exit_code)


@cli.command()
@click.option(
    "--timeout",
    type=float,
    default=10.0,
    show_default=True,
    help="Seconds to wait for the daemon to shut down.",
)
def stop(timeout: float) -> None:
    """Stop the running daemon."""
    cfg = load_config()
    configure_logging(cfg.daemon.log_level)
    ok = daemon_mod.stop(cfg, timeout=timeout)
    if not ok:
        click.echo("timed out waiting for daemon to exit", err=True)
        sys.exit(1)
    click.echo("ok")


@cli.command()
def status() -> None:
    """Report whether the daemon is running."""
    cfg = load_config()
    st = daemon_mod.status(cfg)
    click.echo(st.describe())
    sys.exit(0 if st.running else 3)


@cli.group()
def config() -> None:
    """Inspect agent-master configuration."""


@config.command("show")
def config_show() -> None:
    """Print the active config file path + contents."""
    cfg = load_config()
    click.echo(f"# {cfg.path}")
    click.echo(cfg.path.read_text())


@config.command("path")
def config_path() -> None:
    """Print the path to the active config file."""
    click.echo(str(DEFAULT_CONFIG_PATH))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
