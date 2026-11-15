# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The composed check summary counts a local root served as the finding does
(SR-0348). The summary of a project on its own is in
tests/test_summary_served.py."""
from __future__ import annotations

from throughline.cli import main as tl


def _check(root, capsys) -> tuple[list[str], str]:
    capsys.readouterr()
    tl(["-C", str(root), "check", "--base", ""])
    said = capsys.readouterr()
    return [l for l in said.out.splitlines() if l.startswith("[")], said.err


def test_a_local_root_served_only_by_dead_items_is_not_counted_served(
        consumer_dir, capsys):
    """A full count was printed beside the unserved-root error."""
    assert "1/1 local delivery roots served" in _check(consumer_dir, capsys)[1]
    assert tl(["-C", str(consumer_dir), "status", "SR-0001", "rejected"]) == 0

    findings, summary = _check(consumer_dir, capsys)

    assert any("INT-0001" in line and "unserved-root" in line for line in findings)
    assert "0/1 local delivery roots served" in summary
