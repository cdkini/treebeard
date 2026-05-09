"""`om config` — open the om config file in the configured editor."""

from __future__ import annotations

import click

from om import editor, ui
from om.config import config_path_for, load_config


@click.command("config")
def command() -> None:
    """Open the config file in your configured editor."""
    cfg = load_config()
    path = config_path_for()
    editor.run_editor(cfg.editor, path)
    ui.path(str(path))
