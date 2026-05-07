"""Lightweight unit tests for `om.usage_log.log_invocation`.

End-to-end behavior (the hook firing on real `om` invocations) is
exercised manually; these tests cover the helper directly to keep the
test surface simple.
"""

from __future__ import annotations

import pathlib
import re

from om import usage_log

LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \| (?P<argv>.+)$")


def _write_config(cfg_dir: pathlib.Path, vault: pathlib.Path) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(f'vault = "{vault}"\n', encoding="utf-8")


def test_appends_line_when_vault_exists(tmp_path: pathlib.Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".om").mkdir(parents=True)
    cfg_dir = tmp_path / "cfg"
    _write_config(cfg_dir, vault)

    usage_log.log_invocation(str(cfg_dir), ["init", str(vault)])
    usage_log.log_invocation(str(cfg_dir), ["search", "foo"])

    lines = (vault / ".om" / "usage.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert LINE_RE.match(lines[0]) is not None
    assert lines[0].endswith(f"init {vault}")
    assert LINE_RE.match(lines[1]) is not None
    assert lines[1].endswith("search foo")


def test_silently_skips_when_no_config(tmp_path: pathlib.Path) -> None:
    # Empty cfg dir, no config.toml — must not crash, must not write.
    usage_log.log_invocation(str(tmp_path / "cfg"), ["noop"])
    assert not any(tmp_path.rglob("usage.log"))


def test_silently_skips_when_vault_missing(tmp_path: pathlib.Path) -> None:
    cfg_dir = tmp_path / "cfg"
    _write_config(cfg_dir, tmp_path / "no-such-vault")

    usage_log.log_invocation(str(cfg_dir), ["noop"])
    assert not any(tmp_path.rglob("usage.log"))


def test_silently_skips_when_om_dir_missing(tmp_path: pathlib.Path) -> None:
    # Vault dir exists but the `.om` state dir does not.
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_dir = tmp_path / "cfg"
    _write_config(cfg_dir, vault)

    usage_log.log_invocation(str(cfg_dir), ["noop"])
    assert not (vault / ".om").exists()
