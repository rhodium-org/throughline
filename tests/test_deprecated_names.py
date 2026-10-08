# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""tl-compose says it is deprecated (SR-0247)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import throughline
from throughline.cli import deprecation_notice, main


def _run(monkeypatch, capsys, name):
    """Start the Tool as ``name`` with `--version`, as a console script does."""
    monkeypatch.setattr(sys, "argv", [name, "--version"])
    with pytest.raises(SystemExit) as stop:
        main()
    out = capsys.readouterr()
    return stop.value.code, out.out, out.err


@pytest.mark.parametrize("name", ["tl-compose", "/usr/bin/throughline-compose",
                                  "bin/TL-Compose.EXE"])
def test_a_deprecated_name_prints_one_line_and_changes_nothing_else(
        monkeypatch, capsys, name):
    code, out, err = _run(monkeypatch, capsys, name)
    plain = _run(monkeypatch, capsys, "tl")
    assert (code, out) == plain[:2]
    (line,) = err.splitlines()
    assert "deprecated" in line and "same program as tl" in line
    assert "next major release" in line
    assert plain[2] == ""


@pytest.mark.parametrize("name", ["tl", "throughline", "/x/tl-composer", "pytest"])
def test_any_other_name_prints_no_notice(name):
    assert deprecation_notice(name) is None


def test_a_caller_that_passes_its_own_arguments_gets_no_notice(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["tl-compose"])
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().err == ""


def test_no_message_of_the_tool_tells_a_reader_to_run_tl_compose():
    told = re.compile(r"run `?(tl-compose|throughline-compose)\b")
    hits = [f"{p.name}: {m.group(0)}"
            for p in sorted(Path(throughline.__file__).parent.glob("*.py"))
            for m in told.finditer(p.read_text(encoding="utf-8"))]
    assert hits == []
