"""Registry of external binaries treebeard shells out to.

Every external tool is declared here once with its install hint, so error
messages stay consistent and adding a new dependency is a one-line change.
Callers use `check_required(dep)` at the start of any command that needs
the binary on `$PATH`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from treebeard.ui import TreebeardError


@dataclass(frozen=True)
class Dependency:
    name: str
    install_hint: str
    display_name: str | None = None

    @property
    def label(self) -> str:
        """How the tool is referred to in error messages (e.g., 'ripgrep'
        for the `rg` binary). Defaults to the binary name."""
        return self.display_name or self.name

    def is_available(self) -> bool:
        return shutil.which(self.name) is not None


GIT = Dependency("git", "install via `brew install git` (or your OS package manager)")
FZF = Dependency("fzf", "install via `brew install fzf`")
RG = Dependency("rg", "install via `brew install ripgrep`", display_name="ripgrep")

NVIM = Dependency("nvim", "install via `brew install neovim`", display_name="neovim")
VIM = Dependency("vim", "install via `brew install vim` (or use the system vim)")

BAT = Dependency("bat", "install via `brew install bat`")
GLOW = Dependency("glow", "install via `brew install glow`")
CAT = Dependency("cat", "part of coreutils — should always be present")

EDITORS: tuple[Dependency, ...] = (NVIM, VIM)
PREVIEWERS: tuple[Dependency, ...] = (BAT, GLOW, CAT)
_PREVIEWERS_BY_NAME: dict[str, Dependency] = {dep.name: dep for dep in PREVIEWERS}


def previewer(name: str) -> Dependency:
    """Look up a previewer by name. Raises KeyError on unknown names —
    callers feed values from `PREVIEWERS`, so this should never miss."""
    return _PREVIEWERS_BY_NAME[name]


# Hard requirements: every `treebeard` invocation checks these at CLI startup,
# regardless of which subcommand the user is running. Optional tools
# (alternate editors, alternate previewers) are NOT in here — those are
# resolved at use time against config or fallbacks.
REQUIRED: tuple[Dependency, ...] = (GIT, FZF, RG)


def first_available(deps: tuple[Dependency, ...]) -> Dependency | None:
    """Return the first dependency in `deps` that's on `$PATH`, or None.
    Used by `tb init` to pick a sensible default for the user."""
    return next((dep for dep in deps if dep.is_available()), None)


def check_all() -> None:
    """Raise `TreebeardError` for the first missing required dependency.

    Called at CLI startup. Stops at the first miss because the user can
    only act on one install at a time; listing every miss at once would
    just be noise.
    """
    for dep in REQUIRED:
        if not dep.is_available():
            raise TreebeardError(f"{dep.label} is required", hint=dep.install_hint)


def check_editor(name: str) -> None:
    """Verify the user's configured editor is on `$PATH`.

    Editor is config-driven (any binary), so it can't go in the startup
    `REQUIRED` tuple. Called from `editor.run_editor` right before we
    shell out. If the editor matches a known `EDITORS` entry we use its
    install hint; otherwise we just point at the binary by name.
    """
    for dep in EDITORS:
        if dep.name == name:
            if not dep.is_available():
                raise TreebeardError(f"{dep.label} is required", hint=dep.install_hint)
            return
    if shutil.which(name) is None:
        raise TreebeardError(
            f"editor `{name}` not found on PATH",
            hint="install it, or change `editor` in ~/.treebeard/config.toml",
        )
