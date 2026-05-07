"""`om config` — open the om config file in the configured editor."""

from __future__ import annotations

import click

from om import editor
from om.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG_DIR,
    config_path_for,
    load_config,
)


@click.command("config")
@click.option(
    "--config-dir",
    "config_dir",
    type=click.Path(),
    default=None,
    help=f"Directory holding {CONFIG_FILENAME} (default: {DEFAULT_CONFIG_DIR}).",
)
@click.pass_context
def command(ctx: click.Context, config_dir: str | None) -> None:
    """Open the config file in your configured editor."""
    ctx.ensure_object(dict)["config_dir"] = config_dir
    cfg = load_config(config_dir)
    path = config_path_for(config_dir)
    editor.run_editor(cfg.editor, path)
    click.echo(str(path))
