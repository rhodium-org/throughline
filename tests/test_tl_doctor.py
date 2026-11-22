# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl doctor` says what Python runs tl and how tl was installed (SR-0371)."""

from __future__ import annotations

from pathlib import Path

from throughline import doctor
from throughline.cli import main


def _env(tmp_path: Path, base: str, marker: str | None = None, venv: bool = True,
         uv_dir: str | None = None) -> doctor.Environment:
    prefix = tmp_path / "env"
    prefix.mkdir()
    if marker:
        (prefix / marker).write_text("")
    return doctor.Environment(
        executable=str(prefix / "bin" / "python"),
        version="3.14.4",
        prefix=str(prefix) if venv else base,
        base_prefix=base,
        base_executable=base + "/bin/python3.14",
        uv_python_dir=uv_dir,
    )


def test_pipx_on_the_os_python_is_named_with_its_rebuild(tmp_path):
    env = _env(tmp_path, "/usr", "pipx_metadata.json")
    lines = dict(doctor.report(env, "6.0.0"))
    assert lines["Installed with"] == doctor.PIPX
    assert lines["Python managed by uv"] == "no"
    assert lines["Survives an OS Python upgrade"].startswith("no")
    assert "pipx reinstall-all --python" in lines["Rebuild on a new Python"]
    assert lines["Made from"] == "/usr/bin/python3.14"


def test_uv_tool_on_a_uv_managed_python_survives(tmp_path):
    base = "/home/u/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu"
    env = _env(tmp_path, base, "uv-receipt.toml")
    lines = dict(doctor.report(env, "6.0.0"))
    assert lines["Installed with"] == doctor.UV_TOOL
    assert lines["Python managed by uv"] == "yes"
    assert lines["Survives an OS Python upgrade"].startswith("yes")
    assert "uv tool install --reinstall" in lines["Rebuild on a new Python"]


def test_uv_python_install_dir_is_honoured(tmp_path):
    env = _env(tmp_path, "/opt/pythons/cpython-3.14", "uv-receipt.toml",
               uv_dir="/opt/pythons")
    assert doctor.uv_managed(env)


def test_plain_venv_and_no_environment(tmp_path):
    assert doctor.installer(_env(tmp_path, "/usr")) == doctor.VENV
    bare = doctor.Environment("/usr/bin/python3", "3.14.4", "/usr", "/usr",
                              "/usr/bin/python3")
    assert doctor.installer(bare) == doctor.NONE


def test_command_reads_no_project_and_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    for label in ("tl:", "Python:", "Running on:", "Installed with:",
                  "Rebuild on a new Python:"):
        assert label in out
