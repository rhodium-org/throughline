# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A coverage report per coverage rule (SR-0052).

`tl check` says which items a coverage rule (SR-0042) leaves uncovered, one
finding at a time. It never said how many items a rule applies to, how many of
them are covered, or what share that is, so a project could not tell a rule that
is nearly met from one that matches nothing. `tl coverage` reports each rule with
its counts, its percentage and the UIDs it leaves uncovered.
"""
from __future__ import annotations

import json
from pathlib import Path

from throughline.cli import main as cli_main
from throughline.storage import load_project
from throughline.validate import coverage_report, validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _demo(tmp_path: Path) -> Path:
    """The starter graph: one rule, every requirement needs a verifying test, and
    its one requirement has one."""
    root = tmp_path / "demo"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    return root


def _more(root: Path, n: int) -> None:
    for i in range(n):
        assert _cli(["-C", root, "new", "REQ", "--title", f"more {i}", "--ground",
                     "INT-0001", "--no-interactive"]) == 0


def test_each_rule_is_reported_with_counts_a_percentage_and_what_is_uncovered(tmp_path):
    """SR-0052."""
    root = _demo(tmp_path)
    _more(root, 3)
    (rule,) = coverage_report(load_project(root))
    assert rule.number == 1 and rule.needs == "incoming:verifies"
    assert (rule.matched, rule.covered) == (4, 1)
    assert rule.percent == 25.0
    assert rule.uncovered == ["REQ-0002", "REQ-0003", "REQ-0004"]


def test_the_report_names_exactly_what_the_gate_reports(tmp_path):
    """SR-0052, SR-0042: one reading of a rule. The UIDs the report leaves
    uncovered are the items `tl check` raises the coverage finding on."""
    root = _demo(tmp_path)
    _more(root, 2)
    assert _cli(["-C", root, "delete", "REQ-0003", "--reason", "gone"]) == 0
    project = load_project(root)
    reported = sorted(f.uid for f in validate(project) if f.rule == "coverage")
    assert sorted(coverage_report(project)[0].uncovered) == reported == ["REQ-0002"]


def test_a_rule_that_matches_nothing_has_no_percentage(tmp_path):
    """Nothing to cover is not everything covered."""
    root = _demo(tmp_path)
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[[rules.coverage]]\n'
                   'filter = "type == \'risk\'"\nneeds = "incoming:mitigates"\n',
                   encoding="utf-8")
    rules = coverage_report(load_project(root))
    assert [(r.number, r.matched, r.covered, r.percent) for r in rules] == [
        (1, 1, 1, 100.0), (2, 0, 0, None)]


def test_the_command_prints_the_report(tmp_path, capsys):
    """SR-0052: as text."""
    root = _demo(tmp_path)
    _more(root, 3)
    capsys.readouterr()
    assert _cli(["-C", root, "coverage"]) == 0
    out = capsys.readouterr().out
    assert "incoming:verifies" in out and "1 of 4 covered (25.0%)" in out
    assert "REQ-0002, REQ-0003, REQ-0004" in out


def test_the_command_gives_the_report_to_a_program(tmp_path, capsys):
    """SR-0052: and as JSON."""
    root = _demo(tmp_path)
    _more(root, 1)
    capsys.readouterr()
    assert _cli(["-C", root, "coverage", "--format", "json"]) == 0
    (rule,) = json.loads(capsys.readouterr().out)
    assert rule == {"rule": 1, "filter": "type == 'requirement' and status != 'deleted'",
                    "needs": "incoming:verifies", "severity": "warning",
                    "matched": 2, "covered": 1, "percent": 50.0,
                    "uncovered": ["REQ-0002"]}


def test_a_project_with_no_coverage_rules_says_so(tmp_path, capsys):
    root = tmp_path / "bare"
    assert _cli(["-C", root, "init", "--name", "t", "--bare"]) == 0
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    start = text.index("[[rules.coverage]]")
    end = text.find("\n[", start + 1)
    cfg.write_text(text[:start] + (text[end + 1:] if end != -1 else ""),
                   encoding="utf-8")
    assert not load_project(root).schema.coverage
    capsys.readouterr()
    assert _cli(["-C", root, "coverage"]) == 0
    assert "no coverage rules" in capsys.readouterr().out
    assert _cli(["-C", root, "coverage", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_reporting_writes_nothing(tmp_path):
    root = _demo(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert _cli(["-C", root, "coverage"]) == 0
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
