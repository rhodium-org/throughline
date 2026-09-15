"""Ratify takes several items in one run (UR-0032, SR-0199): every item is checked
before any is signed, the ratifier is asked once, and each item is signed on its own.
These run without a terminal, so the per-item rendering and stop are not exercised
here; the single-item tests that cover them are unchanged in meaning."""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import load_project

CONFIG_EXTRA = '''
[types.intent]
attrs.origin = { type = "enum", values = ["human", "ai", "hybrid"] }
'''


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


@pytest.fixture
def graph(tmp_path) -> Path:
    """An intent and three proposed requirements grounded on it."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + CONFIG_EXTRA, encoding="utf-8")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "V.", "--origin", "human", "--no-interactive"]) == 0
    for n in ("R1", "R2", "R3"):
        assert _cli(["-C", root, "new", "REQ", "--title", n, "--text",
                     f"The Tool shall do {n}.", "--ground", "INT-0001",
                     "--ground-type", "implements", "--origin", "ai",
                     "--no-interactive"]) == 0
    return root


def _ratifiers(root: Path) -> dict[str, str | None]:
    p = load_project(root)
    return {u: p.get(u).attrs.get("ratified_by") for u in ("REQ-0001", "REQ-0002", "REQ-0003")}


def test_several_items_are_ratified_in_one_run_in_the_order_given(graph, capsys):
    assert _cli(["-C", graph, "ratify", "REQ-0002", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["REQ-0002 ratified by Ada Lovelace", "REQ-0001 ratified by Ada Lovelace"]
    assert _ratifiers(graph) == {"REQ-0001": "Ada Lovelace", "REQ-0002": "Ada Lovelace",
                                 "REQ-0003": None}


def test_one_item_still_works_as_before(graph, capsys):
    assert _cli(["-C", graph, "ratify", "REQ-0003", "--by", "Ada Lovelace"]) == 0
    assert capsys.readouterr().out.strip() == "REQ-0003 ratified by Ada Lovelace"


def test_an_unknown_item_refuses_the_whole_run_before_anything_is_written(graph, capsys):
    rc = _cli(["-C", graph, "ratify", "REQ-0001", "REQ-0009", "REQ-0002", "--by", "Ada"])
    assert rc != 0
    assert "REQ-0009 does not exist" in capsys.readouterr().err
    assert _ratifiers(graph) == {"REQ-0001": None, "REQ-0002": None, "REQ-0003": None}


def test_an_item_that_cannot_be_ratified_refuses_the_whole_run(graph, capsys):
    """The obstacle is found for every item before any is signed (SR-0195 for the
    run, not just the first item): here REQ-0002 is already signed by the same
    content, which a plain re-ratification refuses."""
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada Lovelace"]) == 0
    capsys.readouterr()
    rc = _cli(["-C", graph, "ratify", "REQ-0001", "REQ-0002", "REQ-0003", "--by", "Ada Lovelace"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "REQ-0002" in err and "nothing in this run was ratified" in err
    assert _ratifiers(graph) == {"REQ-0001": None, "REQ-0002": "Ada Lovelace", "REQ-0003": None}


def test_a_stale_item_in_the_batch_refuses_the_run_without_accept_change(graph, capsys):
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada Lovelace"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0002", "--text", "The Tool shall do R2 twice."]) == 0
    capsys.readouterr()
    rc = _cli(["-C", graph, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada Lovelace"])
    assert rc != 0
    assert "--accept-change" in capsys.readouterr().err
    assert _ratifiers(graph)["REQ-0001"] is None
    assert _cli(["-C", graph, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada Lovelace",
                 "--accept-change"]) == 0
    assert _ratifiers(graph)["REQ-0001"] == "Ada Lovelace"


def test_no_item_off_a_terminal_is_a_usage_error(graph, capsys):
    assert _cli(["-C", graph, "ratify", "--by", "Ada"]) == 2
    assert "no item given to ratify" in capsys.readouterr().err
