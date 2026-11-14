# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A filter can read an item's standing (SR-0335).

A filter could read signatures through `attrs` until format 4 moved them to the
`ratifications` list (SR-0254), and the names a filter may read were not widened
then. Since then no coverage rule, query or document table could ask whether an
item is signed. Five names give that back, each the judgement `tl check` makes:
`signed`, `stale`, `retired`, `reviewed` and `signers`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.filters import FILTER_NAMES, FilterError, check_filter
from throughline.graph import Index
from throughline.inject import inject_text
from throughline.storage import load_project
from throughline.validate import eval_filter, query_items, validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


@pytest.fixture
def graph(tmp_path) -> Path:
    """The starter graph with REQ-0001 signed by Ada, NFR-0001 signed and then
    reworded, TEST-0001 reviewed, and NG-0001 retired. INT-0001 is untouched."""
    root = tmp_path / "demo"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace",
                 "--by-id", "github:ada"]) == 0
    assert _cli(["-C", root, "ratify", "NFR-0001", "--by", "Ada Lovelace"]) == 0
    assert _cli(["-C", root, "amend", "NFR-0001", "--text", "Reworded since."]) == 0
    assert _cli(["-C", root, "review", "TEST-0001"]) == 0
    assert _cli(["-C", root, "delete", "NG-0001", "--reason", "gone"]) == 0
    return root


def _matching(root: Path, expr: str) -> list[str]:
    project = load_project(root)
    return [it.uid for it in query_items(project, expr, include_deleted=True)]


def test_the_five_names_are_ones_a_filter_may_read():
    """SR-0335, SR-0045."""
    assert {"signed", "stale", "retired", "reviewed", "signers"} <= FILTER_NAMES
    check_filter("signed and not stale and not retired and reviewed "
                 "and 'github:ada' in signers")


def test_signed_is_true_where_the_signatures_meet_what_the_type_requires(graph):
    """SR-0335: as the gate counts it. A signature that no longer matches the
    content counts toward nothing, so an item reworded since it was signed is
    stale and not signed."""
    assert _matching(graph, "signed") == ["REQ-0001"]
    assert _matching(graph, "signed or stale") == ["NFR-0001", "REQ-0001"]


def test_stale_is_true_where_a_signature_no_longer_matches_the_content(graph):
    """SR-0335: as the gate judges it."""
    assert _matching(graph, "stale") == ["NFR-0001"]
    project = load_project(graph)
    assert sorted(f.uid for f in validate(project) if f.rule == "ratified-stale") == [
        "NFR-0001"]


def test_retired_is_true_for_a_tombstone_that_stands(graph):
    """SR-0335."""
    assert _matching(graph, "retired") == ["NG-0001"]
    assert "NG-0001" not in _matching(graph, "not retired")


def test_reviewed_is_true_where_the_review_record_matches_the_content(graph):
    """SR-0335."""
    assert _matching(graph, "reviewed") == ["TEST-0001"]
    assert _cli(["-C", graph, "amend", "TEST-0001", "--text", "Changed after."]) == 0
    assert _matching(graph, "reviewed") == []


def test_signers_holds_each_signer_s_name_and_identifier(graph):
    """SR-0335."""
    assert _matching(graph, "'Ada Lovelace' in signers") == ["NFR-0001", "REQ-0001"]
    assert _matching(graph, "'github:ada' in signers") == ["REQ-0001"]
    assert _matching(graph, "'Bob' in signers") == []


def test_the_names_are_read_by_a_query_a_document_and_a_coverage_rule(graph, capsys):
    """SR-0335, SR-0045: usable identically wherever a filter is."""
    capsys.readouterr()
    assert _cli(["-C", graph, "query", "type == 'requirement' and signed"]) == 0
    assert "REQ-0001" in capsys.readouterr().out

    out = inject_text(load_project(graph),
                      "<!-- tl:count signed and not stale -->\n<!-- tl:end -->\n")
    assert "\n1\n" in out

    cfg = graph / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[[rules.coverage]]\n'
                   'filter = "type == \'requirement\' and signed"\n'
                   'needs = "incoming:relates"\n', encoding="utf-8")
    found = [f for f in validate(load_project(graph)) if f.rule == "coverage"
             and "relates" in f.message]
    assert [f.uid for f in found] == ["REQ-0001"]


def test_standing_is_refused_where_there_is_no_schema_to_judge_it_by(graph):
    """Like an incoming-link test with no index: a refusal, never a silent false."""
    project = load_project(graph)
    item = project.get("REQ-0001")
    with pytest.raises(FilterError, match="signed"):
        eval_filter(item, "signed", Index.build(project))
    assert eval_filter(item, "type == 'requirement'", Index.build(project)) is True


def test_the_signature_record_itself_is_not_readable(graph):
    """SR-0335."""
    for name in ("ratifications", "withdrawals", "content"):
        with pytest.raises(FilterError):
            check_filter(f"{name}")


def test_a_filter_that_names_none_of_them_reads_as_before(graph):
    assert _matching(graph, "type == 'requirement' and status != 'deleted'") == [
        "REQ-0001"]
