# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0031 — a source being fetched announces itself before the fetch, not after.

Cloning a pinned source is the only slow thing composition does, and it happens
before any checking has begun, so a first run against an unfetched source used to
sit silent for the whole of it — the defect UR-0009 names. The obligation is about
*ordering* and *when it stays quiet*, so the tests pin both: the notice precedes the
clone, and a warm cache says nothing at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from throughline import resolvers as resolve_mod
from throughline.resolvers import resolve_source
from throughline.sources import Source

_TOML = '[project]\nname = "src"\nformat_version = 2\n'


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True)


def _origin(tmp_path: Path, tag: str = "v4.0.3") -> Path:
    repo = tmp_path / "origin"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    (repo / "throughline.toml").write_text(_TOML)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    _git("tag", tag, cwd=repo)
    return repo


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))


def test_the_notice_precedes_the_fetch(tmp_path, monkeypatch, capsys):
    """The defect, stated as a test. The probe stands inside the clone and reports
    what had already been said by the time it was reached."""
    said_before: list[str] = []
    real_fetch = resolve_mod._fetch

    def watched(url, ref, dest):
        said_before.append(capsys.readouterr().err)
        return real_fetch(url, ref, dest)

    monkeypatch.setattr(resolve_mod, "_fetch", watched)
    origin = _origin(tmp_path)
    resolve_source(Source(namespace="asvs", url=str(origin), ref="v4.0.3"), tmp_path)

    assert said_before, "_fetch was never reached"
    assert "resolving source 'asvs'" in said_before[0], (
        "nothing was said before the clone began:\n" + repr(said_before[0]))


def test_the_notice_names_the_source_and_where_it_comes_from(tmp_path, capsys):
    origin = _origin(tmp_path)
    resolve_source(Source(namespace="asvs", url=str(origin), ref="v4.0.3"), tmp_path)
    err = capsys.readouterr().err
    assert "asvs" in err
    assert str(origin) in err
    assert "v4.0.3" in err
    assert "resolved source 'asvs'" in err, "the completed fetch is not reported"


def test_a_warm_cache_says_nothing(tmp_path, capsys):
    """The notice earns its place by appearing only on the runs that are slow. A
    resolver that narrated every warm hit would be noise, and noise gets filtered."""
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    resolve_source(src, tmp_path)
    capsys.readouterr()                      # discard the cold-cache notice
    resolve_source(src, tmp_path)
    assert capsys.readouterr().err == ""


def test_a_local_path_source_says_nothing(tmp_path, capsys):
    """A path source is never fetched, so there is nothing to wait for."""
    src = tmp_path / "local"
    src.mkdir()
    (src / "throughline.toml").write_text(_TOML)
    resolve_source(Source(namespace="s", path="local"), tmp_path)
    assert capsys.readouterr().err == ""


def test_the_notice_goes_to_stderr_not_stdout(tmp_path, capsys):
    """Machine-readable output lives on stdout — a progress line there would corrupt
    it for anything parsing the run."""
    origin = _origin(tmp_path)
    resolve_source(Source(namespace="asvs", url=str(origin), ref="v4.0.3"), tmp_path)
    captured = capsys.readouterr()
    assert "resolving source" in captured.err
    assert captured.out == ""
