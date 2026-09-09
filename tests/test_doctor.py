# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The contributor doctor must not rot silently (SR-0075 / UR-0021).

The doctor spent a long time pointing its grounding gate at ``requirements/`` after
the graph moved to ``idd/``, so the check every contributor relied on had been
failing for all of them and reporting it as their problem. Nothing noticed, because
the doctor is the one script no gate runs. These tests are that gate: they assert
the paths it names exist, and that it actually fails an unchained toolchain rather
than passing it quietly.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_doctor():
    """Import ``scripts/doctor.py``, which is not part of the installed package."""
    path = REPO_ROOT / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("_doctor", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves its own module out of sys.modules, so register before exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


doctor = _load_doctor()


@pytest.mark.parametrize("label", ["selfhost", "demo"])
def test_grounding_gate_points_at_a_graph_that_exists(monkeypatch, label):
    """Every path the doctor gates on must be a real throughline graph.

    This is the regression that motivated the file: a moved graph left the check
    failing for everyone while looking like a broken contributor environment.
    """
    seen: list[str] = []
    monkeypatch.setattr(doctor, "_grounding_gate", lambda _l, path: seen.append(path))

    if label == "selfhost":
        doctor.check_grounding_selfhost()
    else:
        doctor.check_grounding_demo()

    (path,) = seen
    assert (REPO_ROOT / path / "throughline.toml").is_file(), (
        f"doctor gates on {path!r}, which holds no throughline.toml"
    )


def test_toolchain_check_fails_when_a_checkout_runs_the_published_build(monkeypatch):
    """A package checked out beside this repo but resolved from PyPI must FAIL.

    That is the silent case UR-0021 exists for: the code imports, the tests pass,
    and the version string names a release the contributor is not running.
    """
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline", "throughline-compose"))
    monkeypatch.setattr(
        Path, "is_file", lambda self: self.name == "pyproject.toml"
    )
    monkeypatch.setattr(
        doctor,
        "_install_kind",
        lambda name: ("editable", str(REPO_ROOT))
        if name == "throughline"
        else ("published", "0.9.0"),
    )

    result = doctor.check_toolchain_chained()

    assert not result.ok
    assert "throughline-compose" in result.detail
    # The remediation must be a command, not an observation.
    assert "pip install" in result.remediation
    assert '-e ".[dev]"' in result.remediation


def test_toolchain_check_passes_when_every_checkout_is_editable(monkeypatch):
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline", "throughline-compose"))
    monkeypatch.setattr(
        Path, "is_file", lambda self: self.name == "pyproject.toml"
    )
    monkeypatch.setattr(
        doctor,
        "_install_kind",
        lambda name: (
            "editable",
            str(REPO_ROOT if name == "throughline" else REPO_ROOT.parent / name),
        ),
    )

    result = doctor.check_toolchain_chained()

    assert result.ok
    # Both paths are the contributor's own checkouts, so neither is flagged.
    assert "(!)" not in result.detail


def test_a_package_absent_from_the_environment_is_not_a_failure(monkeypatch):
    """Not installing compose is a choice, not a divergence — only a published copy
    standing in for a checkout is."""
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline", "throughline-compose"))
    monkeypatch.setattr(
        Path, "is_file", lambda self: self.name == "pyproject.toml"
    )
    monkeypatch.setattr(
        doctor,
        "_install_kind",
        lambda name: ("editable", str(REPO_ROOT))
        if name == "throughline"
        else ("absent", ""),
    )

    assert doctor.check_toolchain_chained().ok


# --- the CLI environments, which are not the one this script runs in --------------

def _fake_cli_venv(tmp_path: Path, name: str) -> Path:
    """A venv laid out as pipx builds one: bin/python symlinked to the base
    interpreter, with the console script beside it."""
    bindir = tmp_path / name / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python").symlink_to(sys.executable)
    script = bindir / "tl"
    script.write_text("#!/bin/sh\n")
    return script


def test_a_cli_in_its_own_venv_is_inspected_not_skipped(tmp_path, monkeypatch):
    """The regression that silently disabled this check.

    A venv's bin/python is a symlink to the base interpreter, so resolving it to
    decide "is this a different environment?" collapses every venv onto the same
    binary — and every separate environment looks like the current one and is
    skipped. Environments must be identified by their venv root instead.
    """
    script = _fake_cli_venv(tmp_path, "pipx-tl")
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline",))
    monkeypatch.setattr(doctor, "CLI_FOR", {"throughline": "tl"})
    monkeypatch.setattr(doctor, "_toolchain_clis", lambda: ["tl"])
    monkeypatch.setattr(doctor.shutil, "which", lambda _c: str(script))
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "pyproject.toml")
    monkeypatch.setattr(
        doctor, "_kinds_in", lambda _p: {"throughline": ["published", "1.9.0"]}
    )

    result = doctor.check_cli_toolchain_chained()

    assert not result.ok, "a separate CLI venv was skipped instead of inspected"
    assert "published" in result.detail
    # pipx has its own remediation: the two traps that both fail silently.
    assert "pipx inject" in result.remediation
    assert "does NOT convert an existing venv" in result.remediation


def test_a_published_plain_name_passes_when_a_suffixed_one_runs_the_tree(
    tmp_path, monkeypatch
):
    """The deliberate two-install arrangement (UR-0021).

    `tl` is the release, kept on the plain name so estate work is gated against a
    published build; `tl-local` runs the working tree. Nothing is standing in for
    anything, so the check must report which command runs which and pass — failing
    here would ban the arrangement outright.
    """
    released = _fake_cli_venv(tmp_path, "pipx-tl")
    local = _fake_cli_venv(tmp_path, "pipx-tl-local")
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline",))
    monkeypatch.setattr(doctor, "CLI_FOR", {"throughline": "tl"})
    monkeypatch.setattr(doctor, "_toolchain_clis", lambda: ["tl", "tl-local"])
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda c: str(local) if c == "tl-local" else str(released),
    )
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "pyproject.toml")
    monkeypatch.setattr(
        doctor,
        "_kinds_in",
        lambda p: {"throughline": ["editable", str(doctor.REPO_ROOT)]}
        if "pipx-tl-local" in str(p)
        else {"throughline": ["published", "2.2.1"]},
    )

    result = doctor.check_cli_toolchain_chained()

    assert result.ok, result.detail
    assert "tl \u2192 throughline: published 2.2.1" in result.detail
    assert "tl-local \u2192 throughline: editable" in result.detail


def test_no_command_runs_the_tree_is_the_only_failure(tmp_path, monkeypatch):
    """Both names published means the contributor's edits run nowhere — the failure
    the check still exists to catch."""
    released = _fake_cli_venv(tmp_path, "pipx-tl")
    local = _fake_cli_venv(tmp_path, "pipx-tl-local")
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline",))
    monkeypatch.setattr(doctor, "CLI_FOR", {"throughline": "tl"})
    monkeypatch.setattr(doctor, "_toolchain_clis", lambda: ["tl", "tl-local"])
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda c: str(local) if c == "tl-local" else str(released),
    )
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "pyproject.toml")
    monkeypatch.setattr(
        doctor, "_kinds_in", lambda _p: {"throughline": ["published", "2.2.1"]}
    )

    result = doctor.check_cli_toolchain_chained()

    assert not result.ok
    assert "No command on PATH runs your working tree" in result.remediation
    assert "pipx --suffix" in result.remediation


def test_the_current_environment_is_not_reported_twice(monkeypatch):
    """The in-process check already judges it; naming it again as a CLI environment
    would report one divergence as two."""
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline",))
    monkeypatch.setattr(doctor, "CLI_FOR", {"throughline": "tl"})
    monkeypatch.setattr(doctor, "_toolchain_clis", lambda: ["tl"])
    monkeypatch.setattr(
        doctor.shutil, "which", lambda _c: str(Path(sys.prefix) / "bin" / "tl")
    )
    monkeypatch.setattr(doctor, "_kinds_in", lambda _p: pytest.fail("re-probed self"))

    result = doctor.check_cli_toolchain_chained()

    assert result.ok
    assert result.detail == "no separate CLI environments"


def test_any_suffix_is_discovered_not_just_a_blessed_one(tmp_path, monkeypatch):
    """Which suffix a second install uses is the contributor's choice.

    Naming one here would publish a private convention as project policy and quietly
    fail everyone who picked a different word, so the extra names are read off PATH.
    A separator is required, or `tlsomething` would be mistaken for the toolchain.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("tl", "tl-wibble", "tl_dev", "tlsomething", "unrelated"):
        (bindir / name).write_text("#!/bin/sh\n")
    monkeypatch.setattr(doctor.os, "get_exec_path", lambda: [str(bindir)])

    found = doctor._toolchain_clis()

    assert "tl-wibble" in found, "an arbitrary suffix must be discovered"
    assert "tl_dev" in found
    assert "tlsomething" not in found, "a separator is required, not a bare prefix"
    assert "unrelated" not in found


def test_a_command_sharing_a_prefix_but_not_the_toolchain_is_ignored(
    tmp_path, monkeypatch
):
    """Discovery is deliberately loose, so the environment has to be the filter —
    otherwise an unrelated `tl-*` tool would be reported as toolchain divergence."""
    ours = _fake_cli_venv(tmp_path, "pipx-tl")
    stranger = _fake_cli_venv(tmp_path, "pipx-tl-unrelated")
    monkeypatch.setattr(doctor, "TOOLCHAIN", ("throughline",))
    monkeypatch.setattr(doctor, "CLI_FOR", {"throughline": "tl"})
    monkeypatch.setattr(doctor, "_toolchain_clis", lambda: ["tl", "tl-unrelated"])
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda c: str(stranger) if c == "tl-unrelated" else str(ours),
    )
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "pyproject.toml")
    monkeypatch.setattr(
        doctor,
        "_kinds_in",
        lambda p: {"throughline": ["absent", ""]}
        if "pipx-tl-unrelated" in str(p)
        else {"throughline": ["editable", str(doctor.REPO_ROOT)]},
    )

    result = doctor.check_cli_toolchain_chained()

    assert result.ok, result.detail
    assert "tl-unrelated" not in result.detail


def test_probe_mode_reports_this_interpreter_as_json(capsys):
    """The probe is how one implementation of the rule is run in another environment
    — so it must stay machine-readable and stdout-clean."""
    assert doctor.main(["--probe"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == set(doctor.TOOLCHAIN)
    kind, _where = payload["throughline"]
    assert kind in {"absent", "editable", "published"}
