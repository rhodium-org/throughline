# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""No status change leaves the tombstone status (SR-0360), and a signed deletion
is permanent while its signature stands (SR-0268).

Found by a proof on 16 November 2026: in a project that declares no transitions,
`tl status` moved a signed tombstone back to a live status and dropped the record
of its deletion.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.grounding import GroundingError, set_status
from throughline.storage import load_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _project(tmp_path: Path, *, transitions: bool) -> Path:
    root = tmp_path / "p"
    assert _cli(["-C", root, "init"]) == 0
    if not transitions:
        cfg = root / "throughline.toml"
        text = cfg.read_text(encoding="utf-8")
        cut = re.sub(r"(?ms)^\[transitions\]\n.*?(?=^\[|\Z)", "", text)
        assert cut != text and "[transitions]" not in cut
        cfg.write_text(cut, encoding="utf-8")
        assert load_project(root).schema.transitions is None
    assert _cli(["-C", root, "ratify", "INT-0001", "REQ-0001", "--by", "Ada"]) == 0
    return root


def _item(root: Path) -> bytes:
    return (root / "requirements" / "REQ-0001.yml").read_bytes()


@pytest.mark.parametrize("transitions", [False, True], ids=["no-table", "table"])
@pytest.mark.parametrize("signed", [False, True], ids=["proposed", "signed"])
def test_status_never_moves_an_item_out_of_the_tombstone_status(
        tmp_path, capsys, transitions, signed):
    root = _project(tmp_path, transitions=transitions)
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    if signed:
        assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    before = _item(root)
    for status in ("proposed", "deferred", "implemented", "rejected", "suspect"):
        capsys.readouterr()
        assert _cli(["-C", root, "status", "REQ-0001", status]) == 2
        err = capsys.readouterr().err
        assert "no status change brings a deleted item back" in err
        assert "`tl restore REQ-0001`" in err and err.count("\n") == 1
        assert _item(root) == before
    item = load_project(root).get("REQ-0001")
    assert item.is_deleted and item.deleted["from"] == "ratified"


def test_the_operation_itself_refuses_so_no_caller_can_leave_the_status(tmp_path):
    root = _project(tmp_path, transitions=False)
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    project = load_project(root)
    item = project.get("REQ-0001")
    with pytest.raises(GroundingError, match="no status change brings"):
        set_status(project.schema, item, "proposed")
    assert item.is_deleted and item.deleted is not None


def test_a_deletion_signed_in_error_is_undone_by_withdrawing_then_restoring(
        tmp_path, capsys):
    """SR-0268: the one way back, and the refusal says so."""
    root = _project(tmp_path, transitions=True)
    live = _item(root)
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    capsys.readouterr()

    assert _cli(["-C", root, "restore", "REQ-0001"]) == 2
    err = capsys.readouterr().err
    assert "permanent while its signature stands" in err
    assert "a tombstone is permanent" not in err
    assert "tl withdraw REQ-0001" in err and "tl restore REQ-0001" in err

    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "signed in error",
                 "--by", "Ada"]) == 0
    assert _cli(["-C", root, "restore", "REQ-0001"]) == 0
    item = load_project(root).get("REQ-0001")
    assert item.status == "ratified" and item.deleted is None
    # The item's own signature was never touched, so it stands over the same words.
    assert [e["by"] for e in item.ratifications] == ["Ada"]
    assert b"withdrawals" in _item(root) and live.split(b"ratifications")[0] in _item(root)


@pytest.mark.parametrize("argv", [
    ["amend", "REQ-0001", "--title", "New"],
    ["flag", "REQ-0001", "--reason", "vague", "--by", "Ada"],
], ids=["amend", "flag"])
def test_no_refusal_calls_a_tombstone_simply_permanent(tmp_path, capsys, argv):
    root = _project(tmp_path, transitions=True)
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, *argv]) == 2
    err = capsys.readouterr().err
    assert "`tl restore REQ-0001`" in err and "is permanent" not in err
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "again"]) == 0
    assert "permanent" not in capsys.readouterr().out
