# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0086 — `tl diagram` emits the type model and the status lifecycle.

The library functions were tested, but the command itself was not: after the
move to `throughline.diagrams` it still called the old private name for the
lifecycle diagram, so every `tl diagram` that asked for transitions (the
default) failed with a NameError. These tests drive the command end to end.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.cli import main as cli_main


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


@pytest.fixture
def graph(tmp_path) -> Path:
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Fast", "--text",
                 "The Tool shall be fast.", "--origin", "ai", "--attr",
                 "priority=must", "--ground", "INT-0001", "--no-interactive"]) == 0
    return root


@pytest.mark.parametrize("kind", ["types", "transitions", "both"])
def test_diagram_command_runs_for_every_kind(graph, kind, capsys):
    capsys.readouterr()
    assert _cli(["-C", graph, "diagram", kind]) == 0
    out = capsys.readouterr().out
    if kind in ("types", "both"):
        assert "### Type model" in out
    if kind in ("transitions", "both"):
        assert "### Status transitions" in out


def test_diagram_default_is_both_as_markdown(graph, capsys):
    capsys.readouterr()
    assert _cli(["-C", graph, "diagram"]) == 0
    out = capsys.readouterr().out
    assert "### Type model" in out and "### Status transitions" in out
    assert "```mermaid" in out


def test_diagram_mermaid_format_is_raw(graph, capsys):
    capsys.readouterr()
    assert _cli(["-C", graph, "diagram", "types", "--format", "mermaid"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("flowchart LR")
    assert "```" not in out
