# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The check summary counts a root served as the finding does (SR-0348).

tl 5.10.0 printed "3/3 delivery roots served" beside an unserved-root error. The
composed summary's half is in tests/composition/test_composed_summary_served.py."""
from __future__ import annotations

from pathlib import Path

from throughline.cli import main as cli_main


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _demo(tmp_path: Path) -> Path:
    root = tmp_path / "g"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    return root


def _check(root: Path, capsys) -> tuple[list[str], str]:
    capsys.readouterr()
    _cli(["-C", root, "check"])
    said = capsys.readouterr()
    return [l for l in said.out.splitlines() if l.startswith("[")], said.err


def _unserved(findings) -> bool:
    return any("INT-0001" in line and "unserved-root" in line for line in findings)


def test_a_root_served_only_by_dead_items_is_not_counted_served(tmp_path, capsys):
    root = _demo(tmp_path)
    assert "1/1 delivery roots served" in _check(root, capsys)[1]
    assert _cli(["-C", root, "status", "NFR-0001", "rejected"]) == 0
    assert "1/1 delivery roots served" in _check(root, capsys)[1]   # one is live
    assert _cli(["-C", root, "status", "REQ-0001", "rejected"]) == 0

    findings, summary = _check(root, capsys)

    assert _unserved(findings)
    assert "0/1 delivery roots served" in summary


def test_a_dead_root_is_counted_as_the_finding_reports_it(tmp_path, capsys):
    """A rejected intent with its rejected requirements is a record kept
    (SR-0344): neither the finding nor the count reports it."""
    root = _demo(tmp_path)
    for uid in ("TEST-0001", "REQ-0001", "NFR-0001", "INT-0001"):
        assert _cli(["-C", root, "status", uid, "rejected"]) == 0

    findings, summary = _check(root, capsys)

    assert not _unserved(findings)
    assert "1/1 delivery roots served" in summary


def test_the_other_figures_of_the_line_are_as_they_were(tmp_path, capsys):
    """SR-0348 changes the served count alone."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "status", "REQ-0001", "rejected"]) == 0

    _findings, summary = _check(root, capsys)

    # REQ-0001 (dead, asked the earlier question) and NFR-0001; not TEST-0001.
    assert "Grounding  2/3 non-root items trace to a root · 1/1 delivery roots served" \
        in summary
