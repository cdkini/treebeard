"""Unit tests for the external-binary registry in `treebeard.dependencies`."""

from __future__ import annotations

import pytest

from treebeard import dependencies as deps
from treebeard.ui import TreebeardError


def test_label_falls_back_to_binary_name() -> None:
    """No display_name → label is the binary name (e.g. `git`)."""
    assert deps.GIT.label == "git"


def test_label_uses_display_name_when_set() -> None:
    """`rg`'s display_name is `ripgrep`, which is what users actually search for."""
    assert deps.RG.label == "ripgrep"
    assert deps.NVIM.label == "neovim"


def test_previewer_lookup_known() -> None:
    assert deps.previewer("bat") is deps.BAT
    assert deps.previewer("cat") is deps.CAT


def test_previewer_lookup_unknown_raises() -> None:
    """Callers feed values from `PREVIEWERS`, so this should never miss in
    practice — but the contract is KeyError, not a silent default."""
    with pytest.raises(KeyError):
        deps.previewer("less")


def test_first_available_returns_first_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Walks the tuple in order; nvim wins over vim when both exist."""
    monkeypatch.setattr(deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert deps.first_available(deps.EDITORS) is deps.NVIM


def test_first_available_skips_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """nvim missing, vim present → falls through to vim."""
    monkeypatch.setattr(
        deps.shutil, "which", lambda name: None if name == "nvim" else f"/usr/bin/{name}"
    )
    assert deps.first_available(deps.EDITORS) is deps.VIM


def test_first_available_returns_none_when_all_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)
    assert deps.first_available(deps.EDITORS) is None


def test_check_all_passes_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    deps.check_all()  # no raise


def test_check_all_raises_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing required binary → TreebeardError with the hint baked into the registry."""
    monkeypatch.setattr(
        deps.shutil, "which", lambda name: None if name == "fzf" else f"/usr/bin/{name}"
    )
    with pytest.raises(TreebeardError) as exc:
        deps.check_all()
    assert "fzf" in str(exc.value)
    assert exc.value.hint is not None
    assert "brew install fzf" in exc.value.hint


def test_check_editor_known_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    deps.check_editor("nvim")  # no raise


def test_check_editor_known_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured editor matches a known EDITORS entry but isn't on PATH —
    error uses the registry's install hint."""
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)
    with pytest.raises(TreebeardError) as exc:
        deps.check_editor("nvim")
    assert "neovim" in str(exc.value)
    assert exc.value.hint is not None
    assert "brew install neovim" in exc.value.hint


def test_check_editor_unknown_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """User configured a non-registry editor (e.g. `code`) and it's not on
    PATH — error points at config, since we have no install hint."""
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)
    with pytest.raises(TreebeardError) as exc:
        deps.check_editor("code")
    assert "code" in str(exc.value)
    assert exc.value.hint is not None
    assert "treebeard config" in exc.value.hint


def test_check_editor_unknown_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-registry editor that IS on PATH passes silently."""
    monkeypatch.setattr(deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    deps.check_editor("code")  # no raise


def test_is_available_uses_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert deps.GIT.is_available()
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)
    assert not deps.GIT.is_available()
