"""`tb config` — open the treebeard config file in the configured editor."""

from __future__ import annotations

import click

from treebeard import editor, ui
from treebeard.config import config_path_for, load_config


@click.command("config")
def command() -> None:
    """Open the config file."""
    cfg = load_config()
    path = config_path_for()
    editor.run_editor(cfg.editor, path)
    ui.path(str(path))
